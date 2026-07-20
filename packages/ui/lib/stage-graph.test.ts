import { describe, expect, it } from "vitest";
import {
	type StageGraphDoc,
	readLoops,
	readStages,
	stageGraphToGraph,
} from "./stage-graph";

/** A minimal valid-shaped stage-graph doc with the given stages/loops. */
function graph(
	stages: Record<string, unknown>[],
	loops: Record<string, unknown>[] = [],
): StageGraphDoc {
	return {
		apiVersion: "swarmkit/v1",
		kind: "StageGraph",
		metadata: { id: "p", name: "P", description: "a pipeline for tests" },
		stages,
		loops,
		provenance: { authored_by: "human", version: "1.0.0" },
	};
}

/** A representative four-stage pipeline wired by signal + a defect loop (design example). */
function sdlc(): StageGraphDoc {
	return graph(
		[
			{
				id: "intake",
				topology: "sdlc/intake",
				when: ["requirement.created"],
				success: "design.kickoff",
			},
			{
				id: "design",
				topology: "sdlc/design",
				when: ["design.kickoff"],
				success: "design.approved",
				gate: "design-approval",
				locks: ["contract:oms-web"],
				release_locks_on: "design.approved",
				compensation: "sdlc/compensate-design",
			},
			{
				id: "build",
				topology: "sdlc/build",
				when: ["design.approved"],
				success: "build.ready",
			},
			{
				id: "sit",
				topology: "sdlc/sit",
				when: ["build.ready"],
				success: "sit.passed",
			},
			{
				id: "defect-triage",
				topology: "sdlc/triage",
				when: [],
				success: "defect.fixed",
			},
			{
				id: "re-test",
				topology: "sdlc/retest",
				when: ["defect.fixed"],
				success: "sit.passed",
			},
		],
		[
			{ when: "defect.raised", to: "defect-triage" },
			{ when: "defect.fixed", to: "re-test" },
		],
	);
}

const edgeIds = (doc: StageGraphDoc) =>
	stageGraphToGraph(doc)
		.edges.map((e) => e.id)
		.sort();

describe("readStages / readLoops", () => {
	it("normalizes stage fields and skips malformed entries", () => {
		const stages = readStages(
			graph([
				{ id: "a", topology: "t", when: ["e1"], success: "e2", locks: ["l"] },
				{ notAStage: true }, // no id → id ""
				"nope", // not an object → dropped
			] as Record<string, unknown>[]),
		);
		expect(stages).toHaveLength(2);
		expect(stages[0]).toMatchObject({
			id: "a",
			topology: "t",
			when: ["e1"],
			success: "e2",
			locks: ["l"],
		});
		expect(stages[1]?.id).toBe("");
	});

	it("reads only well-formed {when, to} loops", () => {
		const loops = readLoops(
			graph(
				[],
				[
					{ when: "defect.raised", to: "triage" },
					{ when: "x" }, // missing `to` → dropped
					{ to: "y" }, // missing `when` → dropped
				],
			),
		);
		expect(loops).toEqual([{ when: "defect.raised", to: "triage" }]);
	});
});

describe("stageGraphToGraph", () => {
	it("returns one node per (identified) stage", () => {
		const g = stageGraphToGraph(sdlc());
		expect(g.nodes.map((n) => n.id)).toEqual([
			"intake",
			"design",
			"build",
			"sit",
			"defect-triage",
			"re-test",
		]);
	});

	it("draws a forward edge where a stage's success matches a later stage's when", () => {
		const g = stageGraphToGraph(
			graph([
				{ id: "a", topology: "t", success: "go" },
				{ id: "b", topology: "t", when: ["go"] },
			]),
		);
		const forward = g.edges.filter((e) => e.kind === "forward");
		expect(forward).toHaveLength(1);
		expect(forward[0]).toMatchObject({
			source: "a",
			target: "b",
			label: "go",
		});
	});

	it("marks a stage with no incoming forward edge as an entry", () => {
		const g = stageGraphToGraph(sdlc());
		const entries = g.nodes.filter((n) => n.data.isEntry).map((n) => n.id);
		// intake (external event) and defect-triage (only reached by an external loop) are entries.
		expect(entries).toContain("intake");
		expect(entries).toContain("defect-triage");
		expect(entries).not.toContain("design");
		expect(entries).not.toContain("build");
	});

	it("lays stages out left→right by longest-path depth", () => {
		const g = stageGraphToGraph(sdlc());
		const x = Object.fromEntries(g.nodes.map((n) => [n.id, n.position.x]));
		expect(x.intake).toBeLessThan(x.design as number);
		expect(x.design).toBeLessThan(x.build as number);
		expect(x.build).toBeLessThan(x.sit as number);
	});

	it("resolves a loop whose `when` a stage emits to a stage→stage edge", () => {
		const g = stageGraphToGraph(sdlc());
		const loop = g.edges.find((e) => e.kind === "loop");
		// defect.fixed is emitted by defect-triage → routes to re-test.
		expect(loop).toMatchObject({
			source: "defect-triage",
			target: "re-test",
			label: "defect.fixed",
		});
	});

	it("surfaces a loop whose `when` no stage emits as an external loop (no edge)", () => {
		const g = stageGraphToGraph(sdlc());
		expect(g.externalLoops).toEqual([
			{ when: "defect.raised", to: "defect-triage" },
		]);
		expect(g.edges.some((e) => e.label === "defect.raised")).toBe(false);
	});

	it("fans out one success signal to every stage that awaits it", () => {
		const doc = graph([
			{ id: "a", topology: "t", success: "go" },
			{ id: "b", topology: "t", when: ["go"] },
			{ id: "c", topology: "t", when: ["go"] },
		]);
		expect(edgeIds(doc)).toEqual(["a->b:go", "a->c:go"]);
	});

	it("ignores a self-triggering stage (success in its own when)", () => {
		const g = stageGraphToGraph(
			graph([{ id: "a", topology: "t", when: ["loop"], success: "loop" }]),
		);
		expect(g.edges).toHaveLength(0);
	});

	it("does not throw on a signal cycle between stages", () => {
		const doc = graph([
			{ id: "a", topology: "t", when: ["y"], success: "x" },
			{ id: "b", topology: "t", when: ["x"], success: "y" },
		]);
		// Both forward edges exist; layout falls back to depth 0 under the cycle guard.
		expect(edgeIds(doc)).toEqual(["a->b:x", "b->a:y"]);
	});

	it("is empty for a doc with no stages", () => {
		const g = stageGraphToGraph(graph([]));
		expect(g.nodes).toEqual([]);
		expect(g.edges).toEqual([]);
		expect(g.externalLoops).toEqual([]);
	});

	it("tolerates a null/undefined document", () => {
		expect(stageGraphToGraph(null).nodes).toEqual([]);
		expect(stageGraphToGraph(undefined).edges).toEqual([]);
	});
});
