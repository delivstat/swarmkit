/**
 * Gate-coverage overlay helpers — map a pipeline's coverage (GET /api/pipelines/{id}/gate-coverage)
 * onto edge colors for the StageGraph canvas. Pure + unit-tested; the canvas is the presentation
 * shell. See design/details/gate-coverage-and-comprehension-debt.md (slice 2).
 */
import type { GateCoverage } from "./types";

export interface StageCoverage {
	gate: "passthrough" | "human";
	external_entry: boolean;
	terminal: boolean;
	/** true for the pipeline's narrowest verified edge (the one to draw the eye to). */
	isNarrowest: boolean;
}

/** CSS var for a forward edge, keyed by its SOURCE stage's gate class. */
export const COVERAGE_COLOR: Record<"passthrough" | "human", string> = {
	passthrough: "var(--destructive)", // red — the pipeline advances unverified by SwarmKit
	human: "var(--success)", // green — a human gate (a funnel) sits on this edge
};

/** Index a coverage payload by stage id, flagging the narrowest edge. Empty when no coverage. */
export function coverageByStage(
	cov: GateCoverage | undefined,
): Map<string, StageCoverage> {
	const map = new Map<string, StageCoverage>();
	if (!cov) return map;
	for (const s of cov.stages) {
		map.set(s.stage, {
			gate: s.gate,
			external_entry: s.external_entry,
			terminal: s.terminal,
			isNarrowest: cov.narrowest === s.stage,
		});
	}
	return map;
}

/** The color for a forward edge, from its SOURCE stage's gate class. undefined ⇒ keep the default. */
export function edgeCoverageColor(
	byStage: Map<string, StageCoverage>,
	sourceStage: string,
): string | undefined {
	const sc = byStage.get(sourceStage);
	return sc ? COVERAGE_COLOR[sc.gate] : undefined;
}
