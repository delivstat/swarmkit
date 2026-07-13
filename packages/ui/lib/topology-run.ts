// Pure trace → per-node run overlay for the topology canvas examine mode
// (design/details/topology-canvas.md). A run is an execution *over* the topology graph: map each
// `agent.step.<id>` span onto its node with duration/cost/tokens/status. Nodes present in the
// topology but absent here "did not fire". Kept pure so it is unit-testable and reused by both UIs.

import type { TraceSpan } from "./types";

export interface NodeRun {
	/** Total wall time across this agent's step span(s), ms. */
	durationMs: number;
	/** Total model cost attributed to this agent, USD. */
	costUsd: number;
	/** Total model tokens (in + out) for this agent. */
	tokens: number;
	/** "error" if any of the agent's steps recorded an error, else "ok". */
	status: "ok" | "error";
}

const STEP_PREFIX = "agent.step.";

/** The agent id encoded in an `agent.step.<id>` span name, or null for any other span. */
export function agentIdFromSpan(name: string): string | null {
	return name.startsWith(STEP_PREFIX) ? name.slice(STEP_PREFIX.length) : null;
}

function num(attributes: Record<string, unknown>, key: string): number {
	const v = attributes[key];
	return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

/**
 * Flatten a run's span tree into a per-agent overlay. An agent that fired more than once
 * accumulates (duration/cost/tokens summed; status = error if any step errored).
 */
export function traceToOverlay(
	root: TraceSpan | null | undefined,
): Record<string, NodeRun> {
	const overlay: Record<string, NodeRun> = {};
	if (!root) return overlay;

	const visit = (span: TraceSpan): void => {
		const id = agentIdFromSpan(span.name);
		if (id) {
			const prev = overlay[id] ?? {
				durationMs: 0,
				costUsd: 0,
				tokens: 0,
				status: "ok",
			};
			overlay[id] = {
				durationMs: prev.durationMs + (span.duration_ms || 0),
				costUsd: prev.costUsd + num(span.attributes, "swarmkit.model.cost_usd"),
				tokens:
					prev.tokens +
					num(span.attributes, "swarmkit.model.tokens_in") +
					num(span.attributes, "swarmkit.model.tokens_out"),
				status: prev.status === "error" || span.error ? "error" : "ok",
			};
		}
		for (const child of span.children ?? []) visit(child);
	};

	visit(root);
	return overlay;
}
