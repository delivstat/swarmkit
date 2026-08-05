/**
 * The dashboard answers questions about the workspace, from data that exists.
 *
 * It read `/jobs` — the in-memory store, holding only what the current serve process started via
 * `POST /run/{topology}` and emptied on restart. A workspace driven from the CLI, a pipeline or
 * chat showed an empty dashboard, and a restart erased whatever was there. Not stale numbers: the
 * wrong source.
 *
 * These cover the arithmetic and, more importantly, the distinctions that must not be flattened —
 * no-rate vs zero-rate, unrecorded vs free, unattributed vs guessed.
 */

import { describe, expect, it } from "vitest";

import {
	bySource,
	byTopology,
	formatCount,
	formatRate,
	formatSpend,
	recentFailures,
	sourceLabel,
	summarize,
	withinHours,
} from "./dashboard";
import type { PersistedJob } from "./types";

const NOW = Date.parse("2026-08-06T12:00:00Z");

function job(over: Partial<PersistedJob> = {}): PersistedJob {
	return {
		job_id: "j1",
		topology: "wms-triage",
		version: null,
		status: "completed",
		created_at: "2026-08-06T11:00:00Z",
		completed_at: "2026-08-06T11:02:00Z",
		usage_input_tokens: 1000,
		usage_output_tokens: 200,
		usage_cost_usd: 0.5,
		correlation_id: null,
		source: "cli",
		...over,
	};
}

describe("withinHours", () => {
	it("keeps runs inside the window", () => {
		const recent = job({ created_at: "2026-08-06T11:00:00Z" });
		const old = job({ job_id: "j2", created_at: "2026-08-01T11:00:00Z" });

		expect(withinHours([recent, old], 24, NOW).map((j) => j.job_id)).toEqual([
			"j1",
		]);
	});

	it("drops a run with an unparseable timestamp rather than counting it as now", () => {
		expect(withinHours([job({ created_at: "not a date" })], 24, NOW)).toEqual(
			[],
		);
	});
});

describe("summarize", () => {
	it("counts by status and totals the spend", () => {
		const stats = summarize([
			job({ status: "completed", usage_cost_usd: 0.5 }),
			job({ job_id: "j2", status: "failed", usage_cost_usd: 0.25 }),
			job({ job_id: "j3", status: "running", usage_cost_usd: null }),
		]);

		expect(stats.total).toBe(3);
		expect(stats.completed).toBe(1);
		expect(stats.failed).toBe(1);
		expect(stats.running).toBe(1);
		expect(stats.costUsd).toBe(0.75);
	});

	it("counts pending as in flight — it has not finished either", () => {
		expect(summarize([job({ status: "pending" })]).running).toBe(1);
	});

	it("computes the failure rate over FINISHED runs, not all runs", () => {
		/** Otherwise a burst of in-flight work makes a failing workspace look healthy. */
		const stats = summarize([
			job({ status: "failed" }),
			job({ job_id: "j2", status: "completed" }),
			job({ job_id: "j3", status: "running" }),
		]);

		expect(stats.failureRate).toBe(0.5);
	});

	it("has no failure rate when nothing has finished", () => {
		/** Null, not zero. A workspace with only in-flight runs has no rate, and 0% would claim
		 * everything is fine. */
		expect(summarize([job({ status: "running" })]).failureRate).toBeNull();
	});

	it("treats missing usage as zero rather than NaN", () => {
		const stats = summarize([
			job({
				usage_cost_usd: null,
				usage_input_tokens: null,
				usage_output_tokens: null,
			}),
		]);

		expect(stats.costUsd).toBe(0);
		expect(stats.inputTokens).toBe(0);
	});
});

describe("bySource", () => {
	it("groups runs and spend by front door", () => {
		const rows = bySource([
			job({ source: "cli", usage_cost_usd: 1 }),
			job({ job_id: "j2", source: "chat", usage_cost_usd: 3 }),
			job({ job_id: "j3", source: "cli", usage_cost_usd: 1 }),
		]);

		expect(rows[0]).toEqual({ source: "chat", runs: 1, costUsd: 3 });
		expect(rows[1]).toEqual({ source: "cli", runs: 2, costUsd: 2 });
	});

	it("keeps rows written before the column existed as unattributed", () => {
		/** Guessing a source would put a real number under the wrong heading, which is worse than
		 * admitting the row predates attribution. */
		const rows = bySource([job({ source: null })]);

		expect(rows[0]?.source).toBeNull();
		expect(sourceLabel(rows[0]?.source ?? null)).toBe("unattributed");
	});
});

describe("byTopology", () => {
	it("ranks topologies by spend and counts their failures", () => {
		const rows = byTopology([
			job({ topology: "cheap", usage_cost_usd: 0.1 }),
			job({ job_id: "j2", topology: "dear", usage_cost_usd: 9 }),
			job({
				job_id: "j3",
				topology: "dear",
				status: "failed",
				usage_cost_usd: 1,
			}),
		]);

		expect(rows[0]?.topology).toBe("dear");
		expect(rows[0]?.costUsd).toBe(10);
		expect(rows[0]?.failed).toBe(1);
	});

	it("limits the list, because a dashboard card is not a report", () => {
		const many = Array.from({ length: 12 }, (_, i) =>
			job({ job_id: `j${i}`, topology: `t${i}`, usage_cost_usd: i }),
		);

		expect(byTopology(many).length).toBe(5);
	});
});

describe("recentFailures", () => {
	it("returns only failures, newest first as history gives them", () => {
		const rows = recentFailures([
			job({ job_id: "ok" }),
			job({ job_id: "bad", status: "failed" }),
		]);

		expect(rows.map((r) => r.job_id)).toEqual(["bad"]);
	});
});

describe("formatting", () => {
	it("shows a dash when there is no rate to state", () => {
		expect(formatRate(null)).toBe("-");
		expect(formatRate(0)).toBe("0%");
	});

	it("compacts large token counts", () => {
		expect(formatCount(950)).toBe("950");
		expect(formatCount(12_400)).toBe("12.4k");
		expect(formatCount(3_200_000)).toBe("3.2M");
	});

	it("distinguishes no runs from free runs", () => {
		/** A workspace that has not run and one whose providers report no cost must not read the
		 * same. */
		expect(formatSpend(0, 0)).toBe("-");
		expect(formatSpend(0, 5)).toBe("$0.00");
	});

	it("does not round a real cost away to nothing", () => {
		expect(formatSpend(0.0004, 1)).toBe("<$0.01");
	});

	it("labels every source", () => {
		expect(sourceLabel("serve")).toBe("API");
		expect(sourceLabel("pipeline")).toBe("Pipeline");
		expect(sourceLabel(null)).toBe("unattributed");
	});
});
