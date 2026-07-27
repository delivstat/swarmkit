import { describe, expect, it } from "vitest";

import { buildMemoryTree, groupByType, memoryKey } from "./memory-tree";
import type { MemoryItem } from "./types";

function mem(
	subject: string,
	attribute: string,
	type = "semantic",
): MemoryItem {
	return {
		key: `${subject}::${attribute}`,
		subject,
		attribute,
		value: "v",
		type,
		confidence: 1,
		valid_from: "t",
		last_reinforced_at: "t",
		reinforce_count: 1,
		source: null,
		status: "active",
	};
}

describe("buildMemoryTree", () => {
	it("groups by subject and sorts subjects + attributes", () => {
		const tree = buildMemoryTree([
			mem("user:bob", "lang"),
			mem("user:alice", "editor"),
			mem("user:alice", "lang"),
		]);
		expect(tree.map((g) => g.subject)).toEqual(["user:alice", "user:bob"]);
		expect(tree[0]?.items.map((m) => m.attribute)).toEqual(["editor", "lang"]);
		expect(tree[1]?.items.map((m) => m.attribute)).toEqual(["lang"]);
	});

	it("handles an empty list", () => {
		expect(buildMemoryTree([])).toEqual([]);
	});
});

describe("groupByType", () => {
	it("buckets by type, sorted", () => {
		const groups = groupByType([
			mem("s", "a", "semantic"),
			mem("s", "b", "profile"),
			mem("s", "c", "profile"),
		]);
		expect(groups.map((g) => g.type)).toEqual(["profile", "semantic"]);
		expect(groups[0]?.items).toHaveLength(2);
	});
});

describe("memoryKey", () => {
	it("matches the store's subject::attribute key", () => {
		expect(memoryKey({ subject: "user:alice", attribute: "lang" })).toBe(
			"user:alice::lang",
		);
	});
});
