/**
 * The Evals views rendered from mocked route responses (design/details/eval-on-serve.md §6):
 * the list, a set's review, a run in progress and a finished report with its diff, and the edit
 * form putting a 422 on the field it belongs to. Static markup — vitest runs in node here.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
	CaseReview,
	DiffCard,
	EvalRunTable,
	EvalSetTable,
	RunHeader,
	RunReport,
} from "@/components/evals";
import { SchemaForm } from "@/components/schema-form";
import {
	type EvalRunDetail,
	type EvalSet,
	caseHistory,
	fieldErrors,
} from "@/lib/evals";
import type { JsonSchema } from "@/lib/schema-form";
import evalSetSchema from "../../schema/schemas/eval-set.schema.json";

const SET: EvalSet = {
	apiVersion: "swarmkit/v1",
	kind: "EvalSet",
	metadata: {
		id: "four",
		description: "The change topology answers each case.",
	},
	target: "change",
	cases: ["one", "two", "three", "four"].map((id) => ({
		id,
		input: `do case-${id}`,
		expect: { contains: ["done"] },
	})),
};

function run(over: Partial<EvalRunDetail> = {}): EvalRunDetail {
	const cases = (over.report?.cases ?? []).length;
	return {
		id: "a1b2c3d4e5f6",
		eval_set_id: "four",
		target: "change",
		status: "succeeded",
		source: "serve",
		passed: 0,
		total: 4,
		recorded: cases,
		cost_usd: 0.008,
		error: null,
		started_at: "2026-11-04T10:00:00Z",
		heartbeat_at: null,
		finished_at: "2026-11-04T10:01:00Z",
		stop_requested_at: null,
		eval_set: SET,
		report: { eval_set_id: "four", target: "change", cases: [] },
		diff: null,
		...over,
	};
}

const result = (case_id: string, passed: boolean) => ({
	case_id,
	passed,
	error: null,
	output: passed ? "done" : "oops",
	cost_usd: 0.002,
	checks: [
		{
			name: "contains:done",
			passed,
			detail: passed ? "" : "'done' not in output",
		},
	],
});

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

describe("EvalSetTable", () => {
	it("lists each set with its target, size and last run", () => {
		const out = html(
			<EvalSetTable
				sets={[
					{
						id: "four",
						name: "",
						description: "d",
						target: "change",
						case_count: 4,
						last_run: {
							id: "r1",
							status: "succeeded",
							passed: 3,
							total: 4,
							pass_rate: 0.75,
							started_at: "2026-11-04T10:00:00Z",
							finished_at: null,
						},
					},
					{
						id: "fresh",
						name: "",
						description: "",
						target: "change",
						case_count: 1,
						last_run: null,
					},
				]}
			/>,
		);
		expect(out).toMatch(/href="\/evals\/?\?set=four"/);
		expect(out).toContain("75% (3/4)");
		expect(out).toMatch(/href="\/evals\/?\?run=r1"/);
		expect(out).toContain("never run");
	});
	it("says where sets come from when there are none", () => {
		expect(html(<EvalSetTable sets={[]} />)).toContain("evals/");
	});
});

describe("EvalRunTable", () => {
	it("marks a run whose set has since been deleted — its history is kept", () => {
		const out = html(
			<EvalRunTable
				runs={[run(), run({ id: "z9", eval_set_id: "gone" })]}
				knownSets={new Set(["four"])}
			/>,
		);
		expect(out.match(/deleted set/g)).toHaveLength(1);
		expect(out).toContain(">serve<");
	});
});

describe("CaseReview", () => {
	it("shows each case's input and expectations, and flags a regression at a glance", () => {
		const history = caseHistory(
			SET.cases.map((c) => c.id),
			[
				run({
					report: {
						eval_set_id: "four",
						target: "change",
						cases: [result("one", true), result("two", false)],
					},
				}),
				run({
					report: {
						eval_set_id: "four",
						target: "change",
						cases: [result("one", true), result("two", true)],
					},
				}),
			],
		);
		const out = html(<CaseReview set={SET} history={history} />);
		expect(out).toContain("do case-three");
		expect(out).toContain("contains &quot;done&quot;");
		expect(out.match(/>regressed</g)).toHaveLength(1);
		expect(out).toMatch(/class="[^"]*bg-destructive\/5[^"]*" data-case="two"/);
		expect(out).not.toMatch(
			/class="[^"]*bg-destructive\/5[^"]*" data-case="one"/,
		);
	});
});

describe("a run in progress", () => {
	const live = run({
		status: "running",
		finished_at: null,
		passed: 2,
		report: {
			eval_set_id: "four",
			target: "change",
			cases: [result("one", true), result("two", true)],
		},
	});
	it("shows progress and offers stop, not resume", () => {
		const out = html(
			<RunHeader run={live} onStop={() => {}} onResume={() => {}} />,
		);
		expect(out).toContain("2 passed · 2 of 4 recorded");
		expect(out).toContain("Stop after this case");
		expect(out).not.toContain("Resume");
	});
	it("lists the cases still to come, the next one as running", () => {
		const out = html(<RunReport run={live} />);
		expect(out).toContain('data-pending="three"');
		expect(out).toContain(">running<");
		expect(out).toContain(">not run<");
	});
	it("says it is stopping once asked, and offers stop no more", () => {
		const out = html(
			<RunHeader
				run={{ ...live, stop_requested_at: "2026-11-04T10:00:30Z" }}
				onStop={() => {}}
			/>,
		);
		expect(out).toContain("stopping after the case in flight");
		expect(out).not.toContain("Stop after this case");
	});
	it("explains the diff comes at the end", () => {
		expect(html(<DiffCard run={live} />)).toContain("when the run finishes");
	});
});

describe("an interrupted run", () => {
	it("offers resume with the number of cases left", () => {
		const out = html(
			<RunHeader
				run={run({
					status: "interrupted",
					recorded: 2,
					finished_at: null,
					error: "the server restarted",
				})}
				onResume={() => {}}
			/>,
		);
		expect(out).toContain("Resume (2 left)");
		expect(out).toContain("the server restarted");
	});
});

describe("a finished report with a regression", () => {
	const done = run({
		passed: 3,
		report: {
			eval_set_id: "four",
			target: "change",
			cases: [
				result("one", true),
				result("two", false),
				result("three", true),
				result("four", true),
			],
		},
		diff: {
			prior_eval_run_id: "prior12345",
			prev_pass_rate: 1,
			curr_pass_rate: 0.75,
			regressed: ["two"],
			fixed: [],
			new: [],
		},
	});
	it("names the regression against the prior run, with both pass rates", () => {
		const out = html(<DiffCard run={done} />);
		expect(out).toMatch(/href="\/evals\/?\?run=prior12345"/);
		expect(out).toContain("100% → 75%");
		expect(out).toMatch(/data-diff="regressed".*two/);
	});
	it("opens a failed case with its checks and output", () => {
		const out = html(<RunReport run={done} />);
		expect(out).toContain("&#x27;done&#x27; not in output");
		expect(out).toContain("oops");
		expect(out.match(/>FAIL</g)).toHaveLength(1);
		expect(out.match(/>PASS</g)).toHaveLength(3);
	});
});

describe("the edit form", () => {
	const schema = evalSetSchema as unknown as JsonSchema;
	it("renders the set from the eval-set schema", () => {
		const out = html(
			<SchemaForm
				schema={schema}
				value={SET as unknown as Record<string, unknown>}
				onChange={() => {}}
			/>,
		);
		expect(out).toContain("cases");
		expect(out).toContain("do case-two");
	});
	it("edits a captured case's reference in a textarea and shows its source", () => {
		const captured = {
			...SET,
			cases: [
				{
					id: "run-3f9a2c1e",
					input: "charged twice",
					expect: { reference: "Routed to billing.", min_confidence: 0.7 },
					source: { run_id: "3f9a2c1e", topology_version: "1.2.0" },
				},
			],
		};
		const out = html(
			<SchemaForm
				schema={schema}
				value={captured as unknown as Record<string, unknown>}
				onChange={() => {}}
			/>,
		);
		expect(out).toMatch(/<textarea[^>]*>Routed to billing\.<\/textarea>/);
		expect(out).toContain('value="3f9a2c1e"');
		expect(out).toContain('value="1.2.0"');
	});
	it("puts a 422 on the field it names, and nowhere else", () => {
		const { byField } = fieldErrors([
			{
				code: "target.unknown",
				message: "Unknown target 'nope'. Known: change",
			},
			{
				code: "schema",
				message: "four.yaml @ /cases/1/input: '' is too short",
			},
		]);
		const out = html(
			<SchemaForm
				schema={schema}
				value={SET as unknown as Record<string, unknown>}
				onChange={() => {}}
				errors={byField}
			/>,
		);
		expect(out).toContain('data-error-path="/target"');
		expect(out).toContain("Unknown target &#x27;nope&#x27;");
		expect(out).toContain('data-error-path="/cases/1/input"');
		expect(out.match(/role="alert"/g)).toHaveLength(2);
	});
});
