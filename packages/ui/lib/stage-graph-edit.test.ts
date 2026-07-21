import { dump, load } from "js-yaml";
import { describe, expect, it } from "vitest";
import { readLoops, readStages } from "./stage-graph";
import {
	addExternalEntry,
	addLock,
	addLoop,
	addStage,
	deleteLoop,
	deleteSignalEdge,
	deriveStageId,
	drawSignalEdge,
	removeLock,
	removeStage,
	removeWhenEvent,
	renameStage,
	setCompensation,
	setGate,
	setLoopWhen,
	setReleaseLocksOn,
	setStageTopology,
} from "./stage-graph-edit";

/** A doc carrying non-visualized fields (metadata/provenance/unknown keys) that must survive edits. */
function doc(
	stages: Record<string, unknown>[] = [],
	loops: Record<string, unknown>[] = [],
): Record<string, unknown> {
	return {
		apiVersion: "swarmkit/v1",
		kind: "StageGraph",
		metadata: { id: "p", name: "P", description: "a pipeline for tests" },
		stages,
		loops,
		provenance: { authored_by: "human", version: "1.0.0" },
		"x-unknown": { keep: "me" },
	};
}

const stage = (id: string, extra: Record<string, unknown> = {}) => ({
	id,
	topology: `t/${id}`,
	when: [],
	...extra,
});

const findStage = (d: Record<string, unknown>, id: string) =>
	readStages(d).find((s) => s.id === id);

describe("deriveStageId", () => {
	it("slugs the topology's last path segment", () => {
		expect(deriveStageId(doc(), "sdlc/design")).toBe("design");
	});
	it("dedupes against existing stage ids", () => {
		const d = doc([stage("design"), stage("design-2")]);
		expect(deriveStageId(d, "sdlc/design")).toBe("design-3");
	});
});

describe("addStage", () => {
	it("appends a stage bound to the dropped topology with a unique id", () => {
		const d = addStage(doc(), "sdlc/design");
		expect(findStage(d, "design")).toMatchObject({
			id: "design",
			topology: "sdlc/design",
		});
	});
	it("does not mutate the input and preserves unknown fields", () => {
		const input = doc();
		const d = addStage(input, "sdlc/design");
		expect(readStages(input)).toHaveLength(0);
		expect(d["x-unknown"]).toEqual({ keep: "me" });
	});
});

describe("renameStage", () => {
	it("sets the id and rewrites loops[].to", () => {
		const d = renameStage(
			doc(
				[stage("design"), stage("build")],
				[{ when: "defect.raised", to: "design" }],
			),
			"design",
			"ux-design",
		);
		expect(findStage(d, "ux-design")).toBeTruthy();
		expect(findStage(d, "design")).toBeUndefined();
		expect(readLoops(d)).toEqual([{ when: "defect.raised", to: "ux-design" }]);
	});
	it("no-ops on a collision, an unchanged id, or a missing stage", () => {
		const d = doc([stage("a"), stage("b")]);
		expect(renameStage(d, "a", "b")).toBe(d); // collision
		expect(renameStage(d, "a", "a")).toBe(d); // unchanged
		expect(renameStage(d, "ghost", "z")).toBe(d); // missing
	});
});

describe("setStageTopology", () => {
	it("rebinds the topology", () => {
		const d = setStageTopology(doc([stage("sit")]), "sit", "oms/sit");
		expect(findStage(d, "sit")?.topology).toBe("oms/sit");
	});
});

describe("drawSignalEdge", () => {
	it("assigns A a default success and adds it to B.when", () => {
		const d = drawSignalEdge(doc([stage("a"), stage("b")]), "a", "b");
		expect(findStage(d, "a")?.success).toBe("a.done");
		expect(findStage(d, "b")?.when).toEqual(["a.done"]);
	});
	it("reuses A's existing success signal (fan-out)", () => {
		const d0 = doc([stage("a", { success: "go" }), stage("b"), stage("c")]);
		const d1 = drawSignalEdge(d0, "a", "b");
		const d2 = drawSignalEdge(d1, "a", "c");
		expect(findStage(d2, "a")?.success).toBe("go"); // one signal
		expect(findStage(d2, "b")?.when).toEqual(["go"]);
		expect(findStage(d2, "c")?.when).toEqual(["go"]);
	});
	it("honors an explicit signal when A has none", () => {
		const d = drawSignalEdge(
			doc([stage("a"), stage("b")]),
			"a",
			"b",
			"design.approved",
		);
		expect(findStage(d, "a")?.success).toBe("design.approved");
		expect(findStage(d, "b")?.when).toEqual(["design.approved"]);
	});
	it("no-ops on a self-edge or a missing endpoint", () => {
		const d = doc([stage("a")]);
		expect(drawSignalEdge(d, "a", "a")).toBe(d);
		expect(drawSignalEdge(d, "a", "ghost")).toBe(d);
	});
	it("does not duplicate an existing when entry", () => {
		const d0 = doc([
			stage("a", { success: "go" }),
			stage("b", { when: ["go"] }),
		]);
		const d1 = drawSignalEdge(d0, "a", "b");
		expect(findStage(d1, "b")?.when).toEqual(["go"]);
	});
});

describe("deleteSignalEdge", () => {
	it("removes the signal from B.when and clears A.success when now unmatched", () => {
		const d0 = doc([
			stage("a", { success: "go" }),
			stage("b", { when: ["go"] }),
		]);
		const d1 = deleteSignalEdge(d0, "a", "b");
		expect(findStage(d1, "b")?.when).toEqual([]);
		expect(findStage(d1, "a")?.success).toBeNull(); // cleared (key deleted)
	});
	it("keeps A.success while another stage still consumes it (fan-out)", () => {
		const d0 = doc([
			stage("a", { success: "go" }),
			stage("b", { when: ["go"] }),
			stage("c", { when: ["go"] }),
		]);
		const d1 = deleteSignalEdge(d0, "a", "b");
		expect(findStage(d1, "b")?.when).toEqual([]);
		expect(findStage(d1, "c")?.when).toEqual(["go"]);
		expect(findStage(d1, "a")?.success).toBe("go"); // c still listens
	});
	it("keeps A.success while a loop still consumes it", () => {
		const d0 = doc(
			[stage("a", { success: "go" }), stage("b", { when: ["go"] })],
			[{ when: "go", to: "a" }],
		);
		const d1 = deleteSignalEdge(d0, "a", "b");
		expect(findStage(d1, "a")?.success).toBe("go");
	});
});

describe("external entries", () => {
	it("adds a when event that no stage emits", () => {
		const d = addExternalEntry(
			doc([stage("intake")]),
			"intake",
			"requirement.created",
		);
		expect(findStage(d, "intake")?.when).toEqual(["requirement.created"]);
	});
	it("removes a when event", () => {
		const d0 = doc([stage("intake", { when: ["requirement.created"] })]);
		const d1 = removeWhenEvent(d0, "intake", "requirement.created");
		expect(findStage(d1, "intake")?.when).toEqual([]);
	});
});

describe("loops", () => {
	it("appends a loop {when, to}", () => {
		const d = addLoop(doc([stage("design")]), "design", "defect.raised");
		expect(readLoops(d)).toEqual([{ when: "defect.raised", to: "design" }]);
	});
	it("no-ops on a duplicate loop or a missing target", () => {
		const d = doc([stage("design")], [{ when: "defect.raised", to: "design" }]);
		expect(addLoop(d, "design", "defect.raised")).toBe(d); // dup
		expect(addLoop(d, "ghost", "x")).toBe(d); // missing target
	});
	it("deletes a loop", () => {
		const d0 = doc(
			[stage("design")],
			[{ when: "defect.raised", to: "design" }],
		);
		const d1 = deleteLoop(d0, "design", "defect.raised");
		expect(readLoops(d1)).toEqual([]);
	});
	it("retargets a loop's trigger event", () => {
		const d0 = doc(
			[stage("design")],
			[{ when: "defect.raised", to: "design" }],
		);
		const d1 = setLoopWhen(d0, "design", "defect.raised", "defect.reopened");
		expect(readLoops(d1)).toEqual([{ when: "defect.reopened", to: "design" }]);
	});
});

describe("per-stage config", () => {
	it("sets and clears the gate", () => {
		const d1 = setGate(doc([stage("design")]), "design", "design-gate");
		expect(findStage(d1, "design")?.gate).toBe("design-gate");
		const d2 = setGate(d1, "design", null);
		expect(findStage(d2, "design")?.gate).toBeNull();
	});
	it("sets and clears the compensation", () => {
		const d1 = setCompensation(doc([stage("design")]), "design", "undo/design");
		expect(findStage(d1, "design")?.compensation).toBe("undo/design");
		expect(
			findStage(setCompensation(d1, "design", null), "design")?.compensation,
		).toBeNull();
	});
	it("sets and clears release_locks_on", () => {
		const d1 = setReleaseLocksOn(
			doc([stage("design")]),
			"design",
			"design.approved",
		);
		expect(findStage(d1, "design")?.releaseLocksOn).toBe("design.approved");
		expect(
			findStage(setReleaseLocksOn(d1, "design", null), "design")
				?.releaseLocksOn,
		).toBeNull();
	});
	it("adds and removes locks, dropping the key when empty", () => {
		const d1 = addLock(doc([stage("design")]), "design", "contract:oms-web");
		expect(findStage(d1, "design")?.locks).toEqual(["contract:oms-web"]);
		expect(addLock(d1, "design", "contract:oms-web")).toBe(d1); // dup no-op
		const d2 = removeLock(d1, "design", "contract:oms-web");
		expect(findStage(d2, "design")?.locks).toEqual([]);
		// key actually dropped, not left as []
		const raw = (d2.stages as Record<string, unknown>[])[0];
		expect(raw).toBeDefined();
		expect(raw && "locks" in raw).toBe(false);
	});
});

describe("removeStage", () => {
	it("removes the stage and any loops that re-enter it", () => {
		const d0 = doc(
			[stage("design"), stage("build")],
			[{ when: "defect.raised", to: "design" }],
		);
		const d1 = removeStage(d0, "design");
		expect(findStage(d1, "design")).toBeUndefined();
		expect(readLoops(d1)).toEqual([]);
	});
});

describe("fidelity + round-trip", () => {
	it("preserves metadata / provenance / unknown keys through an edit", () => {
		const d = drawSignalEdge(
			addStage(addStage(doc(), "sdlc/design"), "sdlc/build"),
			"design",
			"build",
		);
		expect(d.metadata).toEqual({
			id: "p",
			name: "P",
			description: "a pipeline for tests",
		});
		expect(d.provenance).toEqual({ authored_by: "human", version: "1.0.0" });
		expect(d["x-unknown"]).toEqual({ keep: "me" });
	});

	it("document → mutation → document survives a YAML dump/load round-trip", () => {
		const built = setGate(
			addLoop(
				drawSignalEdge(
					addStage(addStage(doc(), "sdlc/design"), "sdlc/build"),
					"design",
					"build",
				),
				"design",
				"defect.raised",
			),
			"design",
			"design-gate",
		);
		const roundTripped = load(dump(built)) as Record<string, unknown>;
		expect(roundTripped).toEqual(built);
	});
});
