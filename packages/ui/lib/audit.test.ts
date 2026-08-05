/**
 * The audit log says what an event was FOR.
 *
 * The table showed event type, agent, topology, run and time — an event's header and none of its
 * content. A reader could see that `skill.executed` happened and never what the skill was asked or
 * what it answered, which is most of why anyone opens the page. Every one of those fields had been
 * in the store since M6; `/audit` returned nine and dropped the rest.
 *
 * These cover the display layer: what goes on the row, what goes in the disclosure, and the
 * distinctions that must not be flattened (unrecorded ≠ allowed, unrecorded ≠ zero).
 */

import { describe, expect, it } from "vitest";

import {
	detailSections,
	formatDuration,
	formatModelCost,
	hasDetail,
	summarize,
	truncate,
} from "./audit";
import type { AuditEvent } from "./types";

function event(over: Partial<AuditEvent> = {}): AuditEvent {
	return {
		event_id: "e1",
		event_type: "skill.executed",
		agent_id: "triage",
		agent_role: "worker",
		timestamp: "2026-08-05T10:00:00Z",
		topology_id: "wms-triage",
		skill_id: "search-wms-tables",
		run_id: "WMS-5:design",
		payload: {},
		policy_decision: "allow",
		policy_reason: null,
		skill_category: "capability",
		inputs: null,
		outputs: null,
		verdict: null,
		reasoning: null,
		confidence: null,
		model_provider: null,
		model_name: null,
		tokens_in: null,
		tokens_out: null,
		cost_usd: null,
		duration_ms: null,
		error: null,
		parent_event_id: null,
		...over,
	};
}

describe("summarize", () => {
	it("shows a decision skill's verdict and confidence", () => {
		const s = summarize(
			event({
				verdict: "fail",
				confidence: 0.82,
				reasoning: "no grounding cited",
			}),
		);

		expect(s).toContain("fail");
		expect(s).toContain("82%");
		expect(s).toContain("no grounding cited");
	});

	it("leads with a denial, which is the row a reader is scanning for", () => {
		const s = summarize(
			event({
				policy_decision: "deny",
				policy_reason: "scope skills:activate is human-only",
			}),
		);

		expect(s).toContain("denied");
		expect(s).toContain("human-only");
	});

	it("otherwise shows what the tool was asked", () => {
		expect(summarize(event({ inputs: { query: "PGM hold" } }))).toBe(
			"query: PGM hold",
		);
	});

	it("reads inputs out of the payload when the event predates the structured fields", () => {
		expect(
			summarize(event({ payload: { inputs: { query: "pick confirm" } } })),
		).toBe("query: pick confirm");
	});

	it("falls back to the skill id rather than an empty cell", () => {
		expect(summarize(event())).toBe("search-wms-tables");
	});

	it("prefers an error over the arguments — a failed call is about the failure", () => {
		expect(
			summarize(event({ inputs: { q: "x" }, error: { message: "timed out" } })),
		).toBe("timed out");
	});
});

describe("detailSections", () => {
	it("orders the sections the way the questions are asked", () => {
		const sections = detailSections(
			event({
				policy_reason: "allowed by role",
				inputs: { query: "PGM" },
				outputs: { result: "3 tables" },
				reasoning: "the tables matched",
			}),
		);

		expect(sections.map((s) => s.label)).toEqual([
			"Policy",
			"Inputs",
			"Outputs",
			"Reasoning",
		]);
	});

	it("formats structured values as readable JSON, not [object Object]", () => {
		const [inputs] = detailSections(
			event({ inputs: { query: "PGM", limit: 40 } }),
		);

		expect(inputs?.body).toContain('"query": "PGM"');
	});

	it("omits sections that hold nothing — an empty dict is not content", () => {
		expect(
			detailSections(event({ inputs: {}, outputs: null, payload: {} })),
		).toEqual([]);
	});

	it("shows the payload last, for events that only ever had one", () => {
		const sections = detailSections(event({ payload: { note: "legacy" } }));

		expect(sections.at(-1)?.label).toBe("Payload");
	});
});

describe("hasDetail", () => {
	it("is false for a header-only event, so it offers no disclosure onto nothing", () => {
		expect(hasDetail(event())).toBe(false);
	});

	it("is true as soon as there is something to read", () => {
		expect(hasDetail(event({ outputs: { result: "3 tables" } }))).toBe(true);
	});
});

describe("formatModelCost", () => {
	it("combines model, tokens and cost", () => {
		const s = formatModelCost(
			event({
				model_name: "claude-opus-5",
				tokens_in: 1200,
				tokens_out: 340,
				cost_usd: 0.42,
			}),
		);

		expect(s).toContain("claude-opus-5");
		expect(s).toContain("1,200 in");
		expect(s).toContain("$0.42");
	});

	it("does not round a real cost away to $0.00", () => {
		expect(formatModelCost(event({ cost_usd: 0.0004 }))).toBe("<$0.01");
	});

	it("is null for an event that is not a model call", () => {
		expect(formatModelCost(event())).toBeNull();
	});
});

describe("formatDuration", () => {
	it("uses ms below a second and seconds above", () => {
		expect(formatDuration(120)).toBe("120ms");
		expect(formatDuration(4200)).toBe("4.2s");
	});

	it("returns null when never recorded — which is not the same as zero", () => {
		expect(formatDuration(null)).toBeNull();
		expect(formatDuration(0)).toBe("0ms");
	});
});

describe("truncate", () => {
	it("leaves a short summary alone", () => {
		expect(truncate("query: PGM")).toBe("query: PGM");
	});

	it("cuts on a word boundary when one is near the limit", () => {
		const out = truncate(`${"word ".repeat(30)}end`, 40);

		expect(out.endsWith("…")).toBe(true);
		expect(out.length).toBeLessThanOrEqual(41);
		expect(out).not.toContain("wor…");
	});
});
