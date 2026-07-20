import { describe, expect, it } from "vitest";
import {
	FUNNEL_LAYERS,
	type FunnelDoc,
	funnelToGraph,
	layerPresent,
} from "./funnel-graph";

/** A minimal valid-shaped funnel doc with the given optional layers present. */
function funnel(layers: Partial<Record<string, unknown>> = {}): FunnelDoc {
	return {
		apiVersion: "swarmkit/v1",
		kind: "Funnel",
		metadata: { id: "g", name: "G", description: "a gate for tests" },
		approve: { rules: [{ scope: "a:b", roles: ["r"], quorum: "any" }] },
		provenance: { authored_by: "human", version: "1.0.0" },
		...layers,
	};
}

const edgeIds = (f: FunnelDoc) =>
	funnelToGraph(f)
		.edges.map((e) => e.id)
		.sort();

describe("layerPresent", () => {
	it("is true only for object-valued optional layers", () => {
		expect(layerPresent(funnel({ judge: { skill: "j" } }), "judge")).toBe(true);
		expect(layerPresent(funnel(), "judge")).toBe(false);
		expect(layerPresent(null, "validate")).toBe(false);
		// a stray non-object value does not count as present
		expect(layerPresent(funnel({ review: "nope" }), "review")).toBe(false);
	});
});

describe("funnelToGraph", () => {
	it("always renders all six layers regardless of which are active (fixed topology)", () => {
		const g = funnelToGraph(funnel());
		expect(g.nodes.map((n) => n.id)).toEqual(FUNNEL_LAYERS);
	});

	it("marks approve/draft/done active always and optional layers per the doc", () => {
		const g = funnelToGraph(funnel({ judge: { skill: "j" } }));
		const active = Object.fromEntries(
			g.nodes.map((n) => [n.id, n.data.present]),
		);
		expect(active).toMatchObject({
			draft: true,
			validate: false,
			judge: true,
			review: false,
			approve: true,
			done: true,
		});
	});

	it("approve is required and can never be toggled off", () => {
		const approve = funnelToGraph(funnel()).nodes.find(
			(n) => n.id === "approve",
		);
		expect(approve?.data.required).toBe(true);
		expect(approve?.data.present).toBe(true);
	});

	it("flags a toggled-on but empty layer as not configured", () => {
		const g = funnelToGraph(funnel({ validate: {} }));
		const validate = g.nodes.find((n) => n.id === "validate");
		expect(validate?.data.present).toBe(true);
		expect(validate?.data.configured).toBe(false);
		const g2 = funnelToGraph(funnel({ validate: { autocorrect: true } }));
		expect(g2.nodes.find((n) => n.id === "validate")?.data.configured).toBe(
			true,
		);
	});

	it("chains only the human gate when no optional layers are active", () => {
		expect(edgeIds(funnel())).toEqual(["approve->done", "draft->approve"]);
	});

	it("routes the forward path continuously through active layers", () => {
		const g = funnelToGraph(funnel({ judge: { skill: "j" } }));
		const forward = g.edges
			.filter((e) => e.kind === "forward")
			.map((e) => e.id);
		expect(forward).toEqual([
			"draft->judge",
			"judge->approve",
			"approve->done",
		]);
	});

	it("adds a retry loop from the last active automated layer back to draft", () => {
		const g = funnelToGraph(funnel({ validate: {}, judge: { skill: "j" } }));
		const retry = g.edges.filter((e) => e.kind === "retry");
		expect(retry).toHaveLength(1);
		expect(retry[0]).toMatchObject({ source: "judge", target: "draft" });
	});

	it("draws an escalate edge only when judge and review are both active", () => {
		expect(
			funnelToGraph(funnel({ judge: { skill: "j" } })).edges.some(
				(e) => e.kind === "escalate",
			),
		).toBe(false);
		const g = funnelToGraph(
			funnel({ judge: { skill: "j" }, review: { archetype: "r" } }),
		);
		const escalate = g.edges.filter((e) => e.kind === "escalate");
		expect(escalate).toHaveLength(1);
		expect(escalate[0]).toMatchObject({ source: "judge", target: "approve" });
	});

	it("labels the edge into the human gate as pass/exhausted", () => {
		const g = funnelToGraph(funnel());
		expect(g.edges.find((e) => e.target === "approve")?.label).toBe(
			"pass · exhausted",
		);
	});

	it("wires the full pipeline when every layer is active", () => {
		const g = funnel({
			validate: { autocorrect: true },
			judge: { skill: "j" },
			review: { archetype: "r" },
		});
		expect(edgeIds(g)).toEqual([
			"approve->done",
			"draft->validate",
			"judge->approve:escalate",
			"judge->review",
			"review->approve",
			"review->draft:retry",
			"validate->judge",
		]);
	});
});
