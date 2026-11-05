/**
 * The Evals page's logic (design/details/eval-on-serve.md §6): where a 422 belongs in the form,
 * which cases moved between runs, and what a run's state lets a person do.
 */

import { describe, expect, it, vi } from "vitest";

import {
	type EvalRunDetail,
	canResumeEval,
	canStopEval,
	caseHistory,
	errorPointer,
	expectLines,
	fieldErrors,
	followEvalRun,
	isLive,
	newEvalSetYaml,
	passRate,
	progressLabel,
} from "./evals";

function report(cases: Record<string, boolean>): Pick<EvalRunDetail, "report"> {
	return {
		report: {
			eval_set_id: "s",
			target: "t",
			cases: Object.entries(cases).map(([case_id, passed]) => ({
				case_id,
				passed,
				error: null,
				output: "",
				cost_usd: 0,
				checks: [],
			})),
		},
	};
}

describe("errorPointer", () => {
	it("reads the JSON Pointer the loader puts in a schema error", () => {
		expect(
			errorPointer(
				"four.yaml @ /cases/1/expect: Additional properties are not allowed ('contain' was unexpected)",
			),
		).toEqual({
			pointer: "/cases/1/expect",
			message:
				"Additional properties are not allowed ('contain' was unexpected)",
		});
	});
	it("maps the whole-document pointer to the root", () => {
		expect(errorPointer("x.yaml @ /: 'target' is a required property")).toEqual(
			{
				pointer: "",
				message: "'target' is a required property",
			},
		);
	});
	it("is null for an error with no pointer", () => {
		expect(errorPointer("x.yaml: case id 'a' is used twice")).toBeNull();
	});
});

describe("fieldErrors", () => {
	it("puts each error on its field, and codes without a pointer on the field they name", () => {
		const out = fieldErrors([
			{
				code: "schema",
				message: "four.yaml @ /cases/0/input: 3 is not of type 'string'",
			},
			{
				code: "target.unknown",
				message: "Unknown target 'nope'. Known: change",
			},
			{ code: "id.mismatch", message: "metadata.id 'b' does not match 'a'" },
		]);
		expect(out.byField).toEqual({
			"/cases/0/input": "3 is not of type 'string'",
			"/target": "Unknown target 'nope'. Known: change",
			"/metadata/id": "metadata.id 'b' does not match 'a'",
		});
		expect(out.general).toEqual([]);
	});
	it("keeps an error with no field — a YAML parse error, a duplicate id — general", () => {
		const out = fieldErrors([
			{ code: "yaml.parse", message: "mapping values are not allowed here" },
			{ code: "schema", message: "four.yaml: case id 'a' is used twice" },
		]);
		expect(out.byField).toEqual({});
		expect(out.general).toHaveLength(2);
	});
	it("puts a root-level schema error above the form, not on a field", () => {
		const out = fieldErrors([
			{ code: "schema", message: "x.yaml @ /: 'cases' is a required property" },
		]);
		expect(out.byField).toEqual({});
		expect(out.general).toEqual(["x.yaml @ /: 'cases' is a required property"]);
	});
	it("joins two errors on the same field", () => {
		const out = fieldErrors([
			{ code: "schema", message: "a @ /target: one" },
			{ code: "target.unknown", message: "two" },
		]);
		expect(out.byField["/target"]).toBe("one; two");
	});
});

describe("caseHistory", () => {
	it("lines each case up against the runs, newest first, null where a run never scored it", () => {
		const h = caseHistory(
			["one", "two", "three"],
			[
				report({ one: true, two: false }),
				report({ one: true, two: true, three: true }),
			],
		);
		expect(h.map((c) => c.results)).toEqual([
			[true, true],
			[false, true],
			[null, true],
		]);
	});
	it("flags a case that passed in the previous scoring run and fails in the latest", () => {
		const h = caseHistory(
			["one", "two"],
			[report({ one: true, two: false }), report({ one: true, two: true })],
		);
		expect(h.find((c) => c.caseId === "two")?.regressed).toBe(true);
		expect(h.find((c) => c.caseId === "one")?.regressed).toBe(false);
	});
	it("compares the runs that scored the case, skipping one that never reached it", () => {
		// The newest run was interrupted before case two; the two before it are what count.
		const h = caseHistory(
			["two"],
			[report({ one: true }), report({ two: false }), report({ two: true })],
		);
		expect(h[0]?.regressed).toBe(true);
	});
	it("does not call a case that has always failed regressed", () => {
		const h = caseHistory(["x"], [report({ x: false }), report({ x: false })]);
		expect(h[0]?.regressed).toBe(false);
	});
	it("has no history for a set never run", () => {
		expect(caseHistory(["x"], [])).toEqual([
			{ caseId: "x", results: [], regressed: false },
		]);
	});
});

describe("run controls", () => {
	it("stops only a running run not already asked to stop", () => {
		expect(canStopEval({ status: "running", stop_requested_at: null })).toBe(
			true,
		);
		expect(
			canStopEval({
				status: "running",
				stop_requested_at: "2026-11-04T10:00:00Z",
			}),
		).toBe(false);
		expect(canStopEval({ status: "succeeded", stop_requested_at: null })).toBe(
			false,
		);
	});
	it("resumes only an interrupted or stopped run — serve 409s anything else", () => {
		for (const s of ["interrupted", "stopped"] as const)
			expect(canResumeEval({ status: s })).toBe(true);
		for (const s of ["running", "succeeded", "failed", "error"] as const)
			expect(canResumeEval({ status: s })).toBe(false);
	});
	it("polls only while running", () => {
		expect(isLive({ status: "running" })).toBe(true);
		expect(isLive({ status: "interrupted" })).toBe(false);
		expect(isLive(null)).toBe(false);
	});
});

describe("labels", () => {
	it("says how much of a cut-short run was recorded", () => {
		expect(
			progressLabel({
				status: "interrupted",
				passed: 2,
				total: 4,
				recorded: 2,
			}),
		).toBe("2 passed · 2 of 4 recorded");
		expect(
			progressLabel({ status: "succeeded", passed: 3, total: 4, recorded: 4 }),
		).toBe("3/4 passed");
	});
	it("prints a pass rate, and a dash for an empty run", () => {
		expect(passRate(3, 4)).toBe("75%");
		expect(passRate(0, 0)).toBe("—");
	});
	it("reads a case's expectations as lines", () => {
		expect(
			expectLines({
				contains: ["done"],
				not_empty: true,
				rubric: "is polite",
				min_confidence: 0.7,
			}),
		).toEqual(['contains "done"', "not empty", "rubric: is polite (≥ 0.7)"]);
		expect(expectLines(undefined)).toEqual([]);
	});
	it("starts a new set on a real topology when there is one", () => {
		expect(newEvalSetYaml("change")).toContain("target: change");
		expect(newEvalSetYaml()).toContain("target: TOPOLOGY");
	});
});

describe("followEvalRun", () => {
	const detail = (status: string, recorded: number) =>
		({ id: "r1", status, recorded, total: 3 }) as unknown as EvalRunDetail;

	it("polls while running, hands over each state, and stops by itself when the run ends", async () => {
		const states = [
			detail("running", 1),
			detail("running", 2),
			detail("succeeded", 3),
		];
		const fetchRun = vi.fn(
			async () => states.shift() ?? detail("succeeded", 3),
		);
		const seen: number[] = [];
		const sleeps: number[] = [];
		const last = await followEvalRun(fetchRun, (r) => seen.push(r.recorded), {
			intervalMs: 10,
			sleep: async (ms) => {
				sleeps.push(ms);
			},
		});
		expect(seen).toEqual([1, 2, 3]);
		expect(last?.status).toBe("succeeded");
		expect(fetchRun).toHaveBeenCalledTimes(3);
		expect(sleeps).toEqual([10, 10]);
	});

	it("fetches a finished run once", async () => {
		const fetchRun = vi.fn(async () => detail("interrupted", 2));
		await followEvalRun(fetchRun, () => {}, { sleep: async () => {} });
		expect(fetchRun).toHaveBeenCalledOnce();
	});

	it("reports a failed fetch and keeps following — serve may be restarting", async () => {
		let n = 0;
		const fetchRun = vi.fn(async () => {
			n += 1;
			if (n === 1) throw new Error("502");
			return detail("succeeded", 3);
		});
		const errors: unknown[] = [];
		const last = await followEvalRun(fetchRun, () => {}, {
			sleep: async () => {},
			onError: (e) => errors.push(e),
		});
		expect(errors).toHaveLength(1);
		expect(last?.status).toBe("succeeded");
	});

	it("stops when aborted, without handing over a late response", async () => {
		const ctrl = new AbortController();
		const onUpdate = vi.fn();
		const fetchRun = vi.fn(async () => {
			ctrl.abort();
			return detail("running", 1);
		});
		expect(
			await followEvalRun(fetchRun, onUpdate, { signal: ctrl.signal }),
		).toBeNull();
		expect(onUpdate).not.toHaveBeenCalled();
	});
});
