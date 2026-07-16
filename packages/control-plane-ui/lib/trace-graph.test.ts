import { describe, expect, it } from "vitest";

import { type RunGraph, agentIdFromSpan, traceToGraph } from "./trace-graph";
import type { TraceSpan } from "./types";

function span(name: string, over: Partial<TraceSpan> = {}): TraceSpan {
	return {
		name,
		start_ns: 0,
		end_ns: 0,
		duration_ms: 0,
		attributes: {},
		children: [],
		...over,
	};
}

// root delegates to two children; one worker errors and has model cost/tokens.
const TRACE: TraceSpan = span("topology.run", {
	children: [
		span("agent.step.root", {
			duration_ms: 12,
			attributes: { "agent.role": "root" },
			children: [
				span("tool.call.delegate", {
					children: [
						span("agent.step.researcher", {
							duration_ms: 8,
							attributes: {
								"agent.role": "worker",
								"swarmkit.model.cost_usd": 0.03,
								"swarmkit.model.tokens_in": 1000,
								"swarmkit.model.tokens_out": 200,
							},
						}),
					],
				}),
				span("agent.step.writer", {
					duration_ms: 5,
					error: "boom",
					attributes: { "agent.role": "worker" },
				}),
			],
		}),
	],
});

function byId(g: RunGraph) {
	return Object.fromEntries(g.nodes.map((n) => [n.id, n]));
}

describe("agentIdFromSpan", () => {
	it("reads the id from an agent.step.<id> span, else null", () => {
		expect(agentIdFromSpan("agent.step.researcher")).toBe("researcher");
		expect(agentIdFromSpan("tool.call.x")).toBeNull();
		expect(agentIdFromSpan("topology.run")).toBeNull();
	});
});

describe("traceToGraph", () => {
	it("builds a node per fired agent with accumulated run stats", () => {
		const nodes = byId(traceToGraph(TRACE));
		expect(Object.keys(nodes).sort()).toEqual(["researcher", "root", "writer"]);
		expect(nodes.researcher?.data).toMatchObject({
			role: "worker",
			durationMs: 8,
			costUsd: 0.03,
			tokens: 1200, // in + out
			status: "ok",
		});
		expect(nodes.writer?.data.status).toBe("error"); // it errored
		expect(nodes.root?.data.role).toBe("root");
	});

	it("derives delegation edges from span nesting (through non-agent spans)", () => {
		const { edges } = traceToGraph(TRACE);
		const set = new Set(edges.map((e) => `${e.source}->${e.target}`));
		expect(set).toEqual(new Set(["root->researcher", "root->writer"]));
	});

	it("lays out a tidy tree — root centred above its children, deeper = lower", () => {
		const nodes = byId(traceToGraph(TRACE));
		expect(nodes.root?.position.y).toBe(0);
		expect(nodes.researcher?.position.y ?? 0).toBeGreaterThan(
			nodes.root?.position.y ?? 0,
		);
		// root x is the mean of its two leaves' x
		const mean =
			((nodes.researcher?.position.x ?? 0) + (nodes.writer?.position.x ?? 0)) /
			2;
		expect(nodes.root?.position.x).toBeCloseTo(mean);
	});

	it("an agent that fires twice accumulates rather than duplicating", () => {
		const t = span("topology.run", {
			children: [
				span("agent.step.root", { duration_ms: 3 }),
				span("agent.step.root", { duration_ms: 4 }),
			],
		});
		const g = traceToGraph(t);
		expect(g.nodes).toHaveLength(1);
		expect(g.nodes[0]?.data.durationMs).toBe(7);
	});

	it("empty / null trace → empty graph", () => {
		expect(traceToGraph(null)).toEqual({ nodes: [], edges: [] });
		expect(traceToGraph(span("topology.run"))).toEqual({
			nodes: [],
			edges: [],
		});
	});
});
