import { describe, expect, it } from "vitest";
import { topologyToGraph } from "./topology-graph";
import type { ResolvedAgent } from "./types";

function agent(
	id: string,
	role: string,
	children: ResolvedAgent[] = [],
	skills: string[] = [],
): ResolvedAgent {
	return { id, role, source_archetype: null, model: null, skills, children };
}

describe("topologyToGraph", () => {
	it("returns empty for a missing root", () => {
		expect(topologyToGraph(null)).toEqual({ nodes: [], edges: [] });
	});

	it("maps a single agent to one node, no edges", () => {
		const g = topologyToGraph(agent("root", "root", [], ["a", "b"]));
		expect(g.edges).toEqual([]);
		expect(g.nodes).toHaveLength(1);
		expect(g.nodes[0]).toMatchObject({
			id: "root",
			type: "agent",
			data: { id: "root", role: "root", skillCount: 2 },
		});
	});

	it("makes an edge per delegation and a node per agent", () => {
		const g = topologyToGraph(
			agent("root", "root", [agent("w1", "worker"), agent("w2", "worker")]),
		);
		expect(g.nodes.map((n) => n.id).sort()).toEqual(["root", "w1", "w2"]);
		expect(g.edges).toEqual([
			{ id: "root->w1", source: "root", target: "w1" },
			{ id: "root->w2", source: "root", target: "w2" },
		]);
	});

	it("flattens nested children", () => {
		const g = topologyToGraph(
			agent("root", "root", [agent("lead", "leader", [agent("w", "worker")])]),
		);
		expect(g.nodes.map((n) => n.id).sort()).toEqual(["lead", "root", "w"]);
		expect(g.edges.map((e) => e.id).sort()).toEqual(["lead->w", "root->lead"]);
	});

	it("lays out depth on the y axis and centres a parent over its children", () => {
		const g = topologyToGraph(
			agent("root", "root", [agent("a", "worker"), agent("b", "worker")]),
		);
		const pos = (id: string) => {
			const n = g.nodes.find((node) => node.id === id);
			if (!n) throw new Error(`no node ${id}`);
			return n.position;
		};
		// children on a deeper layer than the root
		expect(pos("a").y).toBeGreaterThan(pos("root").y);
		expect(pos("b").y).toBe(pos("a").y);
		// parent centred between its two leaves
		expect(pos("root").x).toBeCloseTo((pos("a").x + pos("b").x) / 2);
	});
});
