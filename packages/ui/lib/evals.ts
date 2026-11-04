/**
 * Eval sets and eval runs, as serve returns them (design/details/eval-on-serve.md §5–§6).
 *
 * Types mirror the routes: `GET /api/evals` (sets with their last run), `GET /evals/runs`
 * (`EvalRunRow.summary()`), `GET /evals/runs/{id}` (`.detail()`). The CLI's `eval-runs --json`
 * returns the same row shapes, so nothing here is portal-specific.
 *
 * The functions are the parts of the page worth testing without a browser: where a validation
 * error belongs in the form, which cases moved between runs, and what a run's state allows.
 */

export type EvalRunStatus =
	| "running"
	| "succeeded"
	| "failed"
	| "stopped"
	| "interrupted"
	| "error";

export interface EvalSetLastRun {
	id: string;
	status: EvalRunStatus;
	passed: number;
	total: number;
	pass_rate: number;
	started_at: string;
	finished_at: string | null;
}

export interface EvalSetListItem {
	id: string;
	name: string;
	description: string;
	target: string;
	case_count: number;
	last_run: EvalSetLastRun | null;
}

export interface EvalExpect {
	contains?: string[];
	not_contains?: string[];
	regex?: string;
	equals?: string;
	not_empty?: boolean;
	used_skills?: string[];
	judge?: string;
	rubric?: string;
	min_confidence?: number;
}

export interface EvalCase {
	id: string;
	input: string;
	expect?: EvalExpect;
}

export interface EvalSet {
	apiVersion?: string;
	kind?: string;
	metadata: { id: string; name?: string; description?: string };
	target: string;
	cases: EvalCase[];
}

export interface EvalCheck {
	name: string;
	passed: boolean;
	detail: string;
}

export interface EvalCaseResult {
	case_id: string;
	passed: boolean;
	error: string | null;
	output: string;
	cost_usd: number;
	checks: EvalCheck[];
}

export interface EvalReport {
	eval_set_id: string;
	target: string;
	started_at?: string;
	finished_at?: string;
	cases: EvalCaseResult[];
}

export interface EvalDiff {
	prior_eval_run_id: string | null;
	prev_pass_rate: number;
	curr_pass_rate: number;
	regressed: string[];
	fixed: string[];
	new: string[];
}

export interface EvalRunSummary {
	id: string;
	eval_set_id: string;
	target: string;
	status: EvalRunStatus;
	source: string;
	passed: number;
	total: number;
	recorded: number;
	cost_usd: number;
	error: string | null;
	started_at: string;
	heartbeat_at: string | null;
	finished_at: string | null;
	stop_requested_at: string | null;
}

export interface EvalRunDetail extends EvalRunSummary {
	eval_set: EvalSet;
	report: EvalReport;
	diff: EvalDiff | null;
}

/** A write that did not validate: serve answers 422 and keeps the previous file. */
export interface EvalSetWriteError {
	code: string;
	message: string;
}

export type EvalSetWriteResult =
	| { valid: true; id: string }
	| { valid: false; errors: EvalSetWriteError[] };

/** A run is only ever stopped or resumed — never killed — so these are the whole control set. */
export function canStopEval(
	run: Pick<EvalRunSummary, "status" | "stop_requested_at">,
): boolean {
	return run.status === "running" && !run.stop_requested_at;
}

/** Resume continues from the cases without results; serve refuses anything else with a 409. */
export function canResumeEval(run: Pick<EvalRunSummary, "status">): boolean {
	return run.status === "interrupted" || run.status === "stopped";
}

/** Still worth polling: a run that is running, including one asked to stop that hasn't yet. */
export function isLive(
	run: Pick<EvalRunSummary, "status"> | null | undefined,
): boolean {
	return run?.status === "running";
}

/** "3/4", or "2 of 4 recorded" while a run is in progress or was cut short. */
export function progressLabel(
	run: Pick<EvalRunSummary, "status" | "passed" | "total" | "recorded">,
): string {
	if (run.recorded < run.total) {
		return `${run.passed} passed · ${run.recorded} of ${run.total} recorded`;
	}
	return `${run.passed}/${run.total} passed`;
}

export function passRate(passed: number, total: number): string {
	return total ? `${Math.round((passed / total) * 100)}%` : "—";
}

/**
 * Where a schema error belongs in the form.
 *
 * The loader words a schema failure as `<file> @ /cases/0/expect: <message>` — a JSON Pointer into
 * the document. Returns that pointer and the message after it, or `null` for an error with no field
 * to sit on (bad YAML, an unknown target is on `/target` though — see `fieldErrors`).
 */
export function errorPointer(
	message: string,
): { pointer: string; message: string } | null {
	const m = /\s@\s(\/[^:]*):\s*(.*)$/s.exec(message);
	if (!m) return null;
	const pointer = m[1] ?? "";
	return { pointer: pointer === "/" ? "" : pointer, message: m[2] ?? "" };
}

/**
 * Write errors keyed by the field they belong on, for `SchemaForm`'s `errors` prop.
 *
 * Errors with a pointer go on that field. Two codes name their field without one: an unknown
 * `target`, and an id that doesn't match the URL. Anything else (a parse error) has no field and is
 * returned in `general`, to show above the form.
 */
export function fieldErrors(errors: EvalSetWriteError[]): {
	byField: Record<string, string>;
	general: string[];
} {
	const byField: Record<string, string> = {};
	const general: string[] = [];
	for (const e of errors) {
		const at = errorPointer(e.message);
		const pointer = at?.pointer
			? at.pointer
			: e.code === "target.unknown"
				? "/target"
				: e.code === "id.mismatch" ||
						e.code === "id.invalid" ||
						e.code === "missing.id"
					? "/metadata/id"
					: null;
		if (pointer) {
			const text = at?.message || e.message;
			byField[pointer] = byField[pointer]
				? `${byField[pointer]}; ${text}`
				: text;
		} else {
			general.push(e.message);
		}
	}
	return { byField, general };
}

/** One case's result in each recent run, newest first. `null` where that run never scored it. */
export interface CaseHistory {
	caseId: string;
	results: (boolean | null)[];
	/** Passed in the run before the newest one that scored it, fails in the newest. */
	regressed: boolean;
}

/**
 * Each case of the set against the recent runs, for the review view.
 *
 * `runs` is newest first. A case is "regressed" when the latest run that scored it failed it and the
 * run that scored it before that passed it — the same rule as the stored diff, applied per case so a
 * reader sees it on the row rather than in a list of ids.
 */
export function caseHistory(
	caseIds: string[],
	runs: Pick<EvalRunDetail, "report">[],
): CaseHistory[] {
	const maps = runs.map((r) => {
		const m = new Map<string, boolean>();
		for (const c of r.report?.cases ?? []) m.set(c.case_id, c.passed);
		return m;
	});
	return caseIds.map((caseId) => {
		const results = maps.map((m) =>
			m.has(caseId) ? (m.get(caseId) ?? null) : null,
		);
		const scored = results.filter((x): x is boolean => x !== null);
		return {
			caseId,
			results,
			regressed:
				scored.length >= 2 && scored[0] === false && scored[1] === true,
		};
	});
}

/** The expectations of a case as short readable lines, for the review view. */
export function expectLines(expect: EvalExpect | undefined): string[] {
	if (!expect) return [];
	const out: string[] = [];
	const list = (xs: string[] | undefined) =>
		(xs ?? []).map((x) => JSON.stringify(x)).join(", ");
	if (expect.contains?.length) out.push(`contains ${list(expect.contains)}`);
	if (expect.not_contains?.length)
		out.push(`not contains ${list(expect.not_contains)}`);
	if (expect.equals !== undefined)
		out.push(`equals ${JSON.stringify(expect.equals)}`);
	if (expect.regex) out.push(`matches /${expect.regex}/`);
	if (expect.not_empty) out.push("not empty");
	if (expect.used_skills?.length)
		out.push(`uses ${expect.used_skills.join(", ")}`);
	const conf =
		expect.min_confidence !== undefined ? ` (≥ ${expect.min_confidence})` : "";
	if (expect.judge) out.push(`judged by ${expect.judge}${conf}`);
	if (expect.rubric) out.push(`rubric: ${expect.rubric}${conf}`);
	return out;
}

/** A starting point for a new set, valid once `target` names a topology. */
export function newEvalSetYaml(target = ""): string {
	return `apiVersion: swarmkit/v1
kind: EvalSet
metadata:
  id: my-eval
  description: What this set checks.
target: ${target || "TOPOLOGY"}
cases:
  - id: first
    input: The request to run.
    expect:
      not_empty: true
`;
}

/**
 * Follow a run: fetch it, hand it over, and fetch again every `intervalMs` while it is running.
 * Resolves with the last state once the run is no longer running, or `null` if aborted first.
 *
 * A loop rather than a fixed interval so a slow response never overlaps the next request, and so
 * it stops by itself — a finished run never changes. A fetch that fails is reported and retried on
 * the next tick: a serve restarting under a run is exactly when someone is watching it.
 */
export async function followEvalRun(
	fetchRun: () => Promise<EvalRunDetail>,
	onUpdate: (run: EvalRunDetail) => void,
	opts: {
		intervalMs?: number;
		signal?: AbortSignal;
		onError?: (err: unknown) => void;
		sleep?: (ms: number) => Promise<void>;
	} = {},
): Promise<EvalRunDetail | null> {
	const interval = opts.intervalMs ?? 1500;
	const sleep =
		opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
	for (;;) {
		if (opts.signal?.aborted) return null;
		try {
			const run = await fetchRun();
			if (opts.signal?.aborted) return null;
			onUpdate(run);
			if (!isLive(run)) return run;
		} catch (err) {
			if (opts.signal?.aborted) return null;
			opts.onError?.(err);
		}
		await sleep(interval);
	}
}
