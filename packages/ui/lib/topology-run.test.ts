import { describe, expect, it } from "vitest";
import { agentIdFromSpan, traceToOverlay } from "./topology-run";
import type { TraceSpan } from "./types";

function span(
	name: string,
	attrs: Record<string, unknown>,
	children: TraceSpan[] = [],
	error: string | null = null,
): TraceSpan {
	return {
		name,
		start_ns: 0,
		end_ns: 0,
		duration_ms: typeof attrs.__ms === "number" ? attrs.__ms : 0,
		attributes: attrs,
		error,
		children,
	};
}

describe("agentIdFromSpan", () => {
	it("extracts the id from an agent.step span, null otherwise", () => {
		expect(agentIdFromSpan("agent.step.code-reviewer")).toBe("code-reviewer");
		expect(agentIdFromSpan("tool.call.read_file")).toBeNull();
		expect(agentIdFromSpan("topology.run")).toBeNull();
	});
});

describe("traceToOverlay", () => {
	it("returns empty for no trace", () => {
		expect(traceToOverlay(null)).toEqual({});
	});

	it("maps each agent.step span onto its node with cost/tokens/duration/status", () => {
		const trace = span("topology.run", {}, [
			span(
				"agent.step.root",
				{
					__ms: 10,
					"swarmkit.model.cost_usd": 0.01,
					"swarmkit.model.tokens_in": 100,
					"swarmkit.model.tokens_out": 50,
				},
				[span("tool.call.delegate", {})],
			),
			span(
				"agent.step.worker",
				{
					__ms: 5,
					"swarmkit.model.cost_usd": 0.005,
					"swarmkit.model.tokens_in": 20,
					"swarmkit.model.tokens_out": 10,
				},
				[],
				"boom",
			),
		]);
		const o = traceToOverlay(trace);
		expect(o.root).toEqual({
			durationMs: 10,
			costUsd: 0.01,
			tokens: 150,
			status: "ok",
		});
		expect(o.worker).toEqual({
			durationMs: 5,
			costUsd: 0.005,
			tokens: 30,
			status: "error",
		});
		expect(o["tool.call.delegate"]).toBeUndefined(); // only agent.step spans map to nodes
	});

	it("accumulates an agent that fires more than once", () => {
		const trace = span("topology.run", {}, [
			span("agent.step.w", {
				__ms: 3,
				"swarmkit.model.cost_usd": 0.001,
				"swarmkit.model.tokens_in": 10,
				"swarmkit.model.tokens_out": 0,
			}),
			span(
				"agent.step.w",
				{
					__ms: 4,
					"swarmkit.model.cost_usd": 0.002,
					"swarmkit.model.tokens_in": 5,
					"swarmkit.model.tokens_out": 5,
				},
				[],
				"err",
			),
		]);
		expect(traceToOverlay(trace).w).toEqual({
			durationMs: 7,
			costUsd: 0.003,
			tokens: 20,
			status: "error",
		});
	});
});
