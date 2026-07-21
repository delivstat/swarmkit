// Live validation for the pipeline editor (design/details/pipeline-editor-canvas.md §Validation).
//
// Two tiers, both anchored to a stage so the canvas can render them inline instead of a bare error
// list:
//  1. Structural (ref-check): topology/gate/compensation refer to a real workspace artifact, a
//     stage has a topology at all. These are the resolver errors the runtime would reject on save —
//     surfaced early as inline `error`s.
//  2. Semantic (editor affordances, all `warning`s — never save-blockers): an unreachable stage, a
//     `when` event that is neither an internal signal nor an obvious webhook ("external, or a
//     typo?"), a `release_locks_on` referencing an event no stage emits, and a dangling `success`
//     (emitted but consumed by nothing, and near a listener → probably a typo, not a real terminal).
//
// Pure (no React) so it is unit-testable and shared by the canvas + any future lint surface.

import type { RefOptions } from "./schema-form";
import {
	type StageGraphDoc,
	emittedSignals,
	externalEntries,
	readLoops,
	readStages,
	stageGraphToGraph,
} from "./stage-graph";

export type IssueLevel = "error" | "warning";

export interface StageIssue {
	/** The stage the issue anchors to (a node decoration). */
	stageId: string;
	level: IssueLevel;
	/** A stable code for styling/testing. */
	code:
		| "missing-topology"
		| "unknown-topology"
		| "unknown-gate"
		| "unknown-compensation"
		| "unreachable"
		| "release-locks-unemitted"
		| "external-or-typo"
		| "dangling-success";
	message: string;
	/** For per-event issues (external-or-typo), the offending event. */
	event?: string;
}

/** Levenshtein distance, capped — enough to spot a near-miss typo (`design.approvd` vs
 * `design.approved`) without pulling in a dependency. */
function editDistance(a: string, b: string): number {
	const m = a.length;
	const n = b.length;
	if (Math.abs(m - n) > 3) return 99;
	let prev = Array.from({ length: n + 1 }, (_, i) => i);
	for (let i = 1; i <= m; i += 1) {
		const curr = [i];
		for (let j = 1; j <= n; j += 1) {
			const cost = a[i - 1] === b[j - 1] ? 0 : 1;
			curr[j] = Math.min(
				(curr[j - 1] ?? 0) + 1,
				(prev[j] ?? 0) + 1,
				(prev[j - 1] ?? 0) + cost,
			);
		}
		prev = curr;
	}
	return prev[n] ?? 99;
}

/** The nearest event in `candidates` to `event` that is a plausible typo (close but not equal), or
 * null. Length-guarded so short names don't false-match. */
function nearestTypo(
	event: string,
	candidates: Iterable<string>,
): string | null {
	let best: string | null = null;
	let bestD = Number.POSITIVE_INFINITY;
	const threshold = event.length <= 6 ? 1 : 2;
	for (const c of candidates) {
		if (c === event) continue;
		const d = editDistance(event, c);
		if (d <= threshold && d < bestD) {
			best = c;
			bestD = d;
		}
	}
	return best;
}

/** A ref list is only trustworthy when the runtime actually served it; an empty list means "not
 * loaded", so we skip that ref-check rather than flag every ref as unknown. */
function known(list: string[] | undefined, ref: string): boolean {
	if (!list || list.length === 0) return true; // list unavailable → don't false-alarm
	return list.includes(ref);
}

/**
 * Validate a stage-graph document against the workspace ref options, returning per-stage issues.
 * `refOptions.topology` / `refOptions.funnel` populate the structural ref-checks; when a list is
 * empty (the runtime did not serve it) that check is skipped.
 */
export function validateStageGraph(
	doc: StageGraphDoc,
	refOptions: RefOptions = {},
): StageIssue[] {
	const issues: StageIssue[] = [];
	const stages = readStages(doc).filter((s) => s.id);
	const loops = readLoops(doc);
	const emitted = emittedSignals(doc);
	const externals = externalEntries(doc);
	const topologies = refOptions.topology;
	const funnels = refOptions.funnel;

	// Every event the graph consumes (for typo near-matches on a dangling success).
	const consumed = new Set<string>();
	for (const s of stages) for (const w of s.when) consumed.add(w);
	for (const l of loops) consumed.add(l.when);

	// Reachability over forward edges from the entry stages.
	const projection = stageGraphToGraph(doc);
	const forward = new Map<string, string[]>();
	for (const e of projection.edges)
		if (e.kind === "forward")
			forward.set(e.source, [...(forward.get(e.source) ?? []), e.target]);
	const entries = projection.nodes
		.filter((n) => n.data.isEntry)
		.map((n) => n.id);
	const reachable = new Set<string>(entries);
	const queue = [...entries];
	while (queue.length > 0) {
		const id = queue.shift();
		if (!id) continue;
		for (const next of forward.get(id) ?? [])
			if (!reachable.has(next)) {
				reachable.add(next);
				queue.push(next);
			}
	}

	for (const stage of stages) {
		// 1. Structural ref-checks.
		if (!stage.topology) {
			issues.push({
				stageId: stage.id,
				level: "error",
				code: "missing-topology",
				message: "No topology bound — pick the swarm this stage runs.",
			});
		} else if (!known(topologies, stage.topology)) {
			issues.push({
				stageId: stage.id,
				level: "error",
				code: "unknown-topology",
				message: `Topology "${stage.topology}" is not in this workspace.`,
			});
		}
		if (stage.gate && !known(funnels, stage.gate)) {
			issues.push({
				stageId: stage.id,
				level: "error",
				code: "unknown-gate",
				message: `Gate funnel "${stage.gate}" is not in this workspace.`,
			});
		}
		if (stage.compensation && !known(topologies, stage.compensation)) {
			issues.push({
				stageId: stage.id,
				level: "error",
				code: "unknown-compensation",
				message: `Compensation topology "${stage.compensation}" is not in this workspace.`,
			});
		}

		// 2. Semantic warnings.
		if (!reachable.has(stage.id)) {
			issues.push({
				stageId: stage.id,
				level: "warning",
				code: "unreachable",
				message: "Unreachable — no forward path from a pipeline entry.",
			});
		}
		if (stage.releaseLocksOn && !emitted.has(stage.releaseLocksOn)) {
			issues.push({
				stageId: stage.id,
				level: "warning",
				code: "release-locks-unemitted",
				message: `release_locks_on "${stage.releaseLocksOn}" is never emitted by any stage.`,
			});
		}
		// Dangling success: emitted but consumed by nothing. A genuine terminal is fine and NOT
		// flagged; we warn only when a near-match listener exists (so it reads as a probable typo,
		// which is exactly "unmatched but not terminal").
		if (stage.success && !consumed.has(stage.success)) {
			const near = nearestTypo(stage.success, consumed);
			if (near) {
				issues.push({
					stageId: stage.id,
					level: "warning",
					code: "dangling-success",
					message: `Success "${stage.success}" reaches no stage — did you mean "${near}"?`,
				});
			}
		}
	}

	// External-vs-typo, per inbound entry: a `when` event no stage emits is either a real webhook or
	// a mistyped signal. A warning, never a save-block (design decision).
	for (const ext of externals) {
		const near = nearestTypo(ext.event, emitted);
		issues.push({
			stageId: ext.stage,
			level: "warning",
			code: "external-or-typo",
			event: ext.event,
			message: near
				? `"${ext.event}" matches no stage signal — external webhook, or a typo of "${near}"?`
				: `"${ext.event}" matches no stage signal — external webhook, or a typo?`,
		});
	}

	return issues;
}

/** Group issues by stage id for per-node rendering. */
export function issuesByStage(issues: StageIssue[]): Map<string, StageIssue[]> {
	const out = new Map<string, StageIssue[]>();
	for (const issue of issues)
		out.set(issue.stageId, [...(out.get(issue.stageId) ?? []), issue]);
	return out;
}
