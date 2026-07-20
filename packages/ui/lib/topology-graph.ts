// Pure topology → graph helpers for the topology canvas (design/details/topology-canvas.md).
// A topology IS a graph: agents are nodes, `children` delegation is edges. This flattens the agent
// tree into React-Flow-shaped nodes/edges with a deterministic tidy-tree layout — no layout library
// needed for the tree case. Kept pure (no React) so it is unit-testable and reused by both modes.

import type { ResolvedAgent } from "./types";

/** The data a canvas node card renders (edit mode) or overlays a run onto (examine mode). */
export interface AgentNodeData {
	id: string;
	role: string;
	archetype: string | null;
	skillCount: number;
	/** Whether this agent's output passes through a Funnel quality gate (design/details/gate-funnel.md).
	 * Drives the "gated" badge on the card. */
	hasFunnel: boolean;
	[key: string]: unknown; // React Flow's node data is an open record
}

export interface GraphNode {
	id: string;
	type: "agent";
	position: { x: number; y: number };
	data: AgentNodeData;
}

export interface GraphEdge {
	id: string;
	source: string;
	target: string;
}

export interface TopologyGraph {
	nodes: GraphNode[];
	edges: GraphEdge[];
}

// Layout spacing (px). A layer per delegation depth; leaves spread across the x axis.
const H_GAP = 220;
const V_GAP = 130;

/**
 * Flatten an agent tree into positioned nodes + delegation edges.
 *
 * Layout is a first-pass tidy tree: leaves get sequential x slots; a parent is centred over its
 * children. Deterministic (same topology → same layout), so runs overlay onto stable positions.
 */
export function topologyToGraph(
	root: ResolvedAgent | null | undefined,
): TopologyGraph {
	const nodes: GraphNode[] = [];
	const edges: GraphEdge[] = [];
	if (!root) return { nodes, edges };

	let nextLeaf = 0;

	// Returns the node's x (so a parent can centre over its children).
	function place(agent: ResolvedAgent, depth: number): number {
		const children = agent.children ?? [];
		let x: number;
		if (children.length === 0) {
			x = nextLeaf * H_GAP;
			nextLeaf += 1;
		} else {
			const childXs = children.map((child) => {
				edges.push({
					id: `${agent.id}->${child.id}`,
					source: agent.id,
					target: child.id,
				});
				return place(child, depth + 1);
			});
			x = childXs.reduce((a, b) => a + b, 0) / childXs.length;
		}
		nodes.push({
			id: agent.id,
			type: "agent",
			position: { x, y: depth * V_GAP },
			data: {
				id: agent.id,
				role: agent.role,
				archetype: agent.source_archetype,
				skillCount: agent.skills?.length ?? 0,
				// A funnel id (string) or a staged raw funnel value both count; empty string / null do not.
				hasFunnel:
					typeof agent.funnel === "string"
						? agent.funnel.length > 0
						: agent.funnel != null,
			},
		});
		return x;
	}

	place(root, 0);
	return { nodes, edges };
}
