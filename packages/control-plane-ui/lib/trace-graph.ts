// Pure trace → run graph for the fleet run view (design/details/fleet-run-graph.md).
//
// A run's span tree already encodes everything the graph needs: `agent.step.<id>` spans are the
// agents that fired, and their NESTING is the delegation hierarchy (a child agent's step span sits
// under its caller's). So — unlike the workspace canvas, which overlays a run onto the *topology* —
// the fleet graph is built directly from the trace ("what actually ran"), needing only the federated
// trace endpoint. Kept pure (no React) so it is unit-testable and mirrors @swarmkit/ui's
// topology-graph/topology-run assertions (the contract).

import type { TraceSpan } from "./types";

const STEP_PREFIX = "agent.step.";
const H_GAP = 200;
const V_GAP = 120;

/** The agent id in an `agent.step.<id>` span name, or null for any other span. */
export function agentIdFromSpan(name: string): string | null {
	return name.startsWith(STEP_PREFIX) ? name.slice(STEP_PREFIX.length) : null;
}

function num(attributes: Record<string, unknown>, key: string): number {
	const v = attributes[key];
	return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

/** A node in the run graph — an agent that fired, with its accumulated run stats. */
export interface RunNodeData {
	id: string;
	role: string | null;
	durationMs: number;
	costUsd: number;
	tokens: number;
	status: "ok" | "error";
	[key: string]: unknown; // React Flow node data is an open record
}

export interface RunGraphNode {
	id: string;
	type: "agent";
	position: { x: number; y: number };
	data: RunNodeData;
}

export interface RunGraphEdge {
	id: string;
	source: string;
	target: string;
}

export interface RunGraph {
	nodes: RunGraphNode[];
	edges: RunGraphEdge[];
}

/**
 * Build a delegation graph from a run's span tree. Agents accumulate across their step spans
 * (duration/cost/tokens summed; status = error if any step errored — mirrors `traceToOverlay`).
 * An edge parent→child is added when a child agent's step nests under a parent agent's. Layout is
 * the same first-pass tidy tree the topology canvas uses (leaves get sequential x slots; a parent is
 * centred over its children), so the fleet graph reads like the workspace one.
 */
export function traceToGraph(root: TraceSpan | null | undefined): RunGraph {
	const runs = new Map<string, RunNodeData>();
	const order: string[] = [];
	const children = new Map<string, Set<string>>();
	let rootId: string | null = null;

	const visit = (span: TraceSpan, parentAgent: string | null): void => {
		const id = agentIdFromSpan(span.name);
		let current = parentAgent;
		if (id) {
			let node = runs.get(id);
			if (!node) {
				node = {
					id,
					role: null,
					durationMs: 0,
					costUsd: 0,
					tokens: 0,
					status: "ok",
				};
				runs.set(id, node);
				order.push(id);
			}
			node.durationMs += span.duration_ms || 0;
			node.costUsd += num(span.attributes, "swarmkit.model.cost_usd");
			node.tokens +=
				num(span.attributes, "swarmkit.model.tokens_in") +
				num(span.attributes, "swarmkit.model.tokens_out");
			if (span.error) node.status = "error";
			const role = span.attributes["agent.role"];
			if (typeof role === "string") node.role = role;

			if (parentAgent && parentAgent !== id) {
				let kids = children.get(parentAgent);
				if (!kids) {
					kids = new Set();
					children.set(parentAgent, kids);
				}
				kids.add(id);
			} else if (!parentAgent && rootId === null) {
				rootId = id;
			}
			current = id;
		}
		for (const child of span.children ?? []) visit(child, current);
	};
	if (root) visit(root, null);

	// Tidy-tree layout. A node reached from the root is placed by delegation depth; any agent not
	// reachable from root (defensive — shouldn't happen for a well-formed trace) is placed as its own
	// root so it still renders.
	const pos = new Map<string, { x: number; y: number }>();
	let nextLeaf = 0;
	const place = (id: string, depth: number): number => {
		const kids = [...(children.get(id) ?? [])];
		let x: number;
		if (kids.length === 0) {
			x = nextLeaf * H_GAP;
			nextLeaf += 1;
		} else {
			const xs = kids.map((k) => place(k, depth + 1));
			x = xs.reduce((a, b) => a + b, 0) / xs.length;
		}
		pos.set(id, { x, y: depth * V_GAP });
		return x;
	};
	if (rootId) place(rootId, 0);
	for (const id of order) if (!pos.has(id)) place(id, 0);

	const nodes: RunGraphNode[] = order.map((id) => ({
		id,
		type: "agent",
		position: pos.get(id) ?? { x: 0, y: 0 },
		// biome-ignore lint/style/noNonNullAssertion: id came from runs, so it's present
		data: runs.get(id)!,
	}));
	const edges: RunGraphEdge[] = [];
	for (const [parent, kids] of children) {
		for (const child of kids) {
			edges.push({ id: `${parent}->${child}`, source: parent, target: child });
		}
	}
	return { nodes, edges };
}
