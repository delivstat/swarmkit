/**
 * The jobs page showed only in-flight work.
 *
 * `/jobs` is the in-memory JobStore — this serve process only, gone on restart — and it was the
 * page's sole source. `/jobs/history` existed server-side the whole time and nothing called it, so
 * a restart erased the visible record of every run, and the durable usage and cost the store had
 * been recording were never shown anywhere.
 *
 * The catch is that a job is written to BOTH stores at creation, so the lists overlap while it
 * runs. Two tables without de-duplication would print every running job twice.
 */

import { describe, expect, it } from "vitest";
import { formatCost, formatTokens, formatWhen, jobSections } from "./jobs";
import type { JobListItem, PersistedJob } from "./types";

function live(over: Partial<JobListItem> = {}): JobListItem {
	return {
		job_id: "a1",
		topology: "wms-design",
		version: null,
		status: "running",
		created_at: "2026-08-04T10:00:00Z",
		completed_at: null,
		...over,
	};
}

function past(over: Partial<PersistedJob> = {}): PersistedJob {
	return {
		job_id: "b2",
		topology: "wms-design",
		version: "1.0.0",
		status: "completed",
		created_at: "2026-08-03T10:00:00Z",
		completed_at: "2026-08-03T10:05:00Z",
		usage_input_tokens: 1200,
		usage_output_tokens: 340,
		usage_cost_usd: 0.42,
		...over,
	};
}

describe("jobSections", () => {
	it("shows durable history, which the page never displayed at all", () => {
		const { past: rows } = jobSections([], [past()]);
		expect(rows.map((j) => j.job_id)).toEqual(["b2"]);
	});

	it("does not print a running job twice", () => {
		// The overlap: a job is persisted the moment it starts, so it is in both lists.
		const sections = jobSections(
			[live({ job_id: "a1" })],
			[past({ job_id: "a1" })],
		);
		expect(sections.running.map((j) => j.job_id)).toEqual(["a1"]);
		expect(sections.past).toEqual([]);
	});

	it("keeps a finished job in history once it leaves the live list", () => {
		const sections = jobSections(
			[],
			[past({ job_id: "a1", status: "completed" })],
		);
		expect(sections.past.map((j) => j.job_id)).toEqual(["a1"]);
	});

	it("treats pending as running — it is queued work, not history", () => {
		const sections = jobSections(
			[live({ job_id: "q", status: "pending" })],
			[],
		);
		expect(sections.running.map((j) => j.job_id)).toEqual(["q"]);
	});

	it("moves a completed in-memory job into neither section twice", () => {
		// `/jobs` keeps completed jobs from this process too; they belong to history, and history
		// has them (both stores were written), so the live section must not claim them.
		const sections = jobSections(
			[live({ job_id: "done", status: "completed" })],
			[past({ job_id: "done" })],
		);
		expect(sections.running).toEqual([]);
		expect(sections.past.map((j) => j.job_id)).toEqual(["done"]);
	});

	it("shows the newest running job first", () => {
		// `/jobs` returns oldest-first; the one a reader wants is the one that just started.
		const sections = jobSections(
			[live({ job_id: "older" }), live({ job_id: "newer" })],
			[],
		);
		expect(sections.running.map((j) => j.job_id)).toEqual(["newer", "older"]);
	});

	it("survives either source being unavailable", () => {
		// Each polls independently: one erroring must not blank the other.
		expect(jobSections(null, [past()]).past).toHaveLength(1);
		expect(jobSections([live()], null).running).toHaveLength(1);
		expect(jobSections(null, null)).toEqual({ running: [], past: [] });
	});
});

describe("formatCost", () => {
	it("distinguishes an unmeasured run from a free one", () => {
		// $0.00 for "we never recorded it" is the kind of confident-looking blank this codebase
		// keeps getting bitten by.
		expect(formatCost(null)).toBe("-");
		expect(formatCost(0)).toBe("$0.00");
	});

	it("does not round a real cost away to nothing", () => {
		expect(formatCost(0.004)).toBe("<$0.01");
		expect(formatCost(0.42)).toBe("$0.42");
		expect(formatCost(12.5)).toBe("$12.50");
	});
});

describe("formatTokens", () => {
	it("is a dash only when neither count was recorded", () => {
		expect(formatTokens(null, null)).toBe("-");
		expect(formatTokens(0, 0)).toBe("0 / 0");
	});

	it("shows a partial record rather than hiding it", () => {
		expect(formatTokens(1200, null)).toBe("1,200 / 0");
	});
});

describe("formatWhen", () => {
	it("dashes an unfinished job rather than inventing a time", () => {
		expect(formatWhen(null)).toBe("-");
		expect(formatWhen("")).toBe("-");
	});

	it("renders a real timestamp", () => {
		expect(formatWhen("2026-08-03T10:05:00Z")).not.toBe("-");
	});
});
