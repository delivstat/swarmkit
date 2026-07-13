import { dump, load } from "js-yaml";
import { describe, expect, it } from "vitest";
import {
	type RawAgent,
	addChild,
	removeAgent,
	reparent,
} from "./topology-edit";

// A raw tree with non-structural fields that must survive every edit.
function tree(): RawAgent {
	return {
		id: "root",
		role: "root",
		prompt: { system: "coordinate" },
		children: [
			{
				id: "lead",
				role: "leader",
				archetype: "eng-lead",
				children: [{ id: "w1", role: "worker" }],
			},
			{ id: "qa", role: "leader" },
		],
	};
}

function ids(root: RawAgent): string[] {
	const out = [root.id];
	for (const c of root.children ?? []) out.push(...ids(c));
	return out.sort();
}

describe("addChild", () => {
	it("adds under the named parent", () => {
		const next = addChild(tree(), "qa", { id: "test-analyst", role: "worker" });
		expect(ids(next)).toContain("test-analyst");
		expect(next.children?.[1]?.children?.[0]?.id).toBe("test-analyst");
	});

	it("no-ops on a missing parent or a duplicate id", () => {
		const t = tree();
		expect(addChild(t, "ghost", { id: "x", role: "worker" })).toBe(t);
		expect(addChild(t, "root", { id: "w1", role: "worker" })).toBe(t); // dup
	});

	it("does not mutate the input", () => {
		const t = tree();
		addChild(t, "root", { id: "new", role: "worker" });
		expect(ids(t)).not.toContain("new");
	});
});

describe("removeAgent", () => {
	it("removes an agent and its subtree", () => {
		const next = removeAgent(tree(), "lead");
		expect(ids(next)).toEqual(["qa", "root"]); // lead + w1 gone
	});

	it("refuses to remove the root", () => {
		const t = tree();
		expect(removeAgent(t, "root")).toBe(t);
	});
});

describe("reparent", () => {
	it("moves an agent under a new parent (draw a delegation edge)", () => {
		const next = reparent(tree(), "w1", "qa");
		expect(next.children?.[0]?.children ?? []).toHaveLength(0); // left lead
		expect(next.children?.[1]?.children?.[0]?.id).toBe("w1"); // now under qa
	});

	it("refuses the root, a cycle, or a no-op", () => {
		const t = tree();
		expect(reparent(t, "root", "qa")).toBe(t); // root
		expect(reparent(t, "lead", "w1")).toBe(t); // cycle: lead under its own child
		expect(reparent(t, "w1", "lead")).toBe(t); // already there
	});
});

describe("YAML round-trip fidelity", () => {
	it("preserves untouched fields through a dump→load cycle", () => {
		const edited = addChild(tree(), "qa", {
			id: "test-analyst",
			role: "worker",
		});
		const roundTripped = load(dump(edited)) as RawAgent;
		// the edit landed
		expect(ids(roundTripped)).toContain("test-analyst");
		// and the root's non-structural prompt survived verbatim
		expect((roundTripped.prompt as { system: string }).system).toBe(
			"coordinate",
		);
		// as did the leader's archetype
		expect(roundTripped.children?.[0]?.archetype).toBe("eng-lead");
	});
});
