import { describe, expect, it } from "vitest";
import type { StageGraphDoc } from "./stage-graph";
import { validateStageGraph } from "./stage-graph-validate";

function graph(
	stages: Record<string, unknown>[],
	loops: Record<string, unknown>[] = [],
): StageGraphDoc {
	return { stages, loops };
}

const codesFor = (
	issues: ReturnType<typeof validateStageGraph>,
	stageId: string,
) => issues.filter((i) => i.stageId === stageId).map((i) => i.code);

describe("validateStageGraph — structural refs", () => {
	it("flags a missing topology", () => {
		const issues = validateStageGraph(graph([{ id: "a", when: [] }]));
		expect(codesFor(issues, "a")).toContain("missing-topology");
	});
	it("flags an unknown topology / gate / compensation against the workspace lists", () => {
		const issues = validateStageGraph(
			graph([
				{
					id: "a",
					topology: "ghost/topo",
					gate: "ghost-gate",
					compensation: "ghost/comp",
				},
			]),
			{ topology: ["real/topo"], funnel: ["real-gate"] },
		);
		const codes = codesFor(issues, "a");
		expect(codes).toContain("unknown-topology");
		expect(codes).toContain("unknown-gate");
		expect(codes).toContain("unknown-compensation");
	});
	it("skips a ref-check when the workspace list is unavailable (empty)", () => {
		const issues = validateStageGraph(
			graph([{ id: "a", topology: "anything/at-all" }]),
			{ topology: [], funnel: [] },
		);
		expect(codesFor(issues, "a")).not.toContain("unknown-topology");
	});
});

describe("validateStageGraph — semantic warnings", () => {
	it("warns on an unreachable stage (no forward path from an entry)", () => {
		// `orphan` listens for a signal no stage emits AND has no forward in-edge → but it IS an entry
		// (indegree 0). To be genuinely unreachable a stage must have a forward in-edge from an
		// unreachable predecessor. Simplest unreachable: a 2-cycle with no external entry into it.
		const issues = validateStageGraph(
			graph([
				{ id: "entry", topology: "t", when: ["kick"], success: "go" },
				{ id: "a", topology: "t", when: ["b.done"], success: "a.done" },
				{ id: "b", topology: "t", when: ["a.done"], success: "b.done" },
			]),
		);
		// a and b only feed each other (a cycle) with no entry path → unreachable.
		expect(codesFor(issues, "a")).toContain("unreachable");
		expect(codesFor(issues, "b")).toContain("unreachable");
		expect(codesFor(issues, "entry")).not.toContain("unreachable");
	});

	it("warns when release_locks_on names an event no stage emits", () => {
		const issues = validateStageGraph(
			graph([
				{
					id: "a",
					topology: "t",
					when: ["kick"],
					release_locks_on: "never.emitted",
				},
			]),
		);
		expect(codesFor(issues, "a")).toContain("release-locks-unemitted");
	});

	it("warns on a dangling success that is a near-miss of a listener (typo, not terminal)", () => {
		const issues = validateStageGraph(
			graph([
				{ id: "a", topology: "t", when: ["kick"], success: "design.approvd" },
				{ id: "b", topology: "t", when: ["design.approved"] },
			]),
		);
		expect(codesFor(issues, "a")).toContain("dangling-success");
	});

	it("does NOT warn on a genuine terminal (unmatched success, no near-match listener)", () => {
		const issues = validateStageGraph(
			graph([
				{ id: "a", topology: "t", when: ["kick"], success: "go" },
				{
					id: "b",
					topology: "t",
					when: ["go"],
					success: "all.finished.cleanly",
				},
			]),
		);
		expect(codesFor(issues, "b")).not.toContain("dangling-success");
	});

	it("flags a when event no stage emits as external-or-typo (a warning, not an error)", () => {
		const issues = validateStageGraph(
			graph([{ id: "intake", topology: "t", when: ["requirement.created"] }]),
		);
		const ext = issues.find((i) => i.code === "external-or-typo");
		expect(ext).toMatchObject({
			stageId: "intake",
			event: "requirement.created",
			level: "warning",
		});
	});

	it("leans 'typo of X' when an external entry is a near-miss of an emitted signal", () => {
		const issues = validateStageGraph(
			graph([
				{ id: "a", topology: "t", when: ["kick"], success: "design.approved" },
				{ id: "b", topology: "t", when: ["design.approvd"] },
			]),
		);
		const ext = issues.find(
			(i) => i.code === "external-or-typo" && i.stageId === "b",
		);
		expect(ext?.message).toContain("typo of");
	});
});
