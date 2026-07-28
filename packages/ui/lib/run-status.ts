// Pure per-stage run-status helpers for the pipeline Runs replay canvas
// (design/details/bundled-pipeline-orchestrator.md §4).
//
// A saga carries its progress as data — `passed_stages`, the `current_stage`, the
// `pending_gate_stage`, and an overall `status`. The replay canvas colours each stage of the
// StageGraph by folding that run state onto the stage id. Kept pure (no React) so it is
// unit-testable and reused by the canvas + inspector without duplication.

import type { SagaDetail, SagaSummary } from "./types";

/** A stage's status *within one run* — distinct from the saga's overall status. */
export type RunStageStatus =
	| "passed" // this stage completed and the run moved on
	| "active" // the run is executing this stage right now
	| "parked" // this stage produced its artifact and waits on its human gate
	| "rejected" // this stage's gate rejected — terminal for the run
	| "failed" // this stage errored / was denied — terminal for the run
	| "pending"; // not reached yet

/** Fold a run's state onto one stage id. Precedence: a passed stage stays passed even after the run
 * later fails elsewhere; the parked gate wins for the stage it parks on; the current stage reflects
 * the run's terminal disposition (failed/rejected) or is simply active. */
export function stageStatus(
	saga: Pick<
		SagaSummary,
		"status" | "current_stage" | "passed_stages" | "pending_gate_stage"
	>,
	stageId: string,
): RunStageStatus {
	if (saga.passed_stages.includes(stageId)) return "passed";
	if (saga.pending_gate_stage === stageId) return "parked";
	if (saga.current_stage === stageId) {
		if (saga.status === "failed") return "failed";
		if (saga.status === "rejected") return "rejected";
		return "active";
	}
	return "pending";
}

/** Display metadata per stage status: a label plus a hex colour that reads on both themes (the ring
 * + dot on a run's node). Semantic colours — separate from the app accent. */
export const RUN_STAGE_META: Record<
	RunStageStatus,
	{ label: string; color: string }
> = {
	passed: { label: "Passed", color: "#22c55e" },
	active: { label: "Active", color: "#0ea5e9" },
	parked: { label: "Parked on gate", color: "#f59e0b" },
	rejected: { label: "Rejected", color: "#ef4444" },
	failed: { label: "Failed", color: "#ef4444" },
	pending: { label: "Pending", color: "#94a3b8" },
};

/** The timeline entries that name a given stage, in sequence order — the per-node history the
 * inspector renders. */
export function stageTimeline(saga: SagaDetail, stageId: string) {
	return saga.timeline
		.filter((t) => t.stage === stageId)
		.sort((a, b) => a.seq - b.seq);
}
