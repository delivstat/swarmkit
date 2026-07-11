import { describe, expect, it } from "vitest";
import { fieldKind, objectFields, resolveRef } from "./schema-form";

const ROOT = {
	$defs: {
		metadata: {
			type: "object",
			description: "artifact metadata",
			properties: { id: { type: "string" }, name: { type: "string" } },
			required: ["id"],
		},
	},
	properties: {
		apiVersion: { const: "swarmkit/v1" },
		category: { enum: ["capability", "decision"] },
		metadata: { $ref: "#/$defs/metadata" },
		enabled: { type: "boolean" },
		retries: { type: "integer" },
		prompt: { type: "string" },
		name: { type: "string" },
		tags: { type: "array", items: { type: "string" } },
	},
	required: ["apiVersion", "metadata"],
};

describe("resolveRef", () => {
	it("resolves a local $ref to its target", () => {
		const r = resolveRef(ROOT, { $ref: "#/$defs/metadata" });
		expect(r.type).toBe("object");
		expect(r.required).toEqual(["id"]);
	});

	it("lets sibling keys override the target", () => {
		const r = resolveRef(ROOT, {
			$ref: "#/$defs/metadata",
			description: "overridden",
		});
		expect(r.description).toBe("overridden");
	});

	it("returns a node without $ref unchanged", () => {
		expect(resolveRef(ROOT, { type: "string" })).toEqual({ type: "string" });
	});

	it("returns unresolvable refs as-is (no crash)", () => {
		expect(resolveRef(ROOT, { $ref: "#/$defs/missing" })).toEqual({
			$ref: "#/$defs/missing",
		});
	});
});

describe("fieldKind", () => {
	it("classifies the common node shapes", () => {
		expect(fieldKind({ const: "swarmkit/v1" })).toBe("const");
		expect(fieldKind({ enum: ["a", "b"] })).toBe("enum");
		expect(fieldKind({ type: "boolean" })).toBe("boolean");
		expect(fieldKind({ type: "integer" })).toBe("number");
		expect(fieldKind({ type: "string" })).toBe("string");
		expect(fieldKind({ type: "string" }, "prompt")).toBe("text");
		expect(fieldKind({ type: "array", items: {} })).toBe("array");
		expect(fieldKind({ type: "object", properties: {} })).toBe("object");
		expect(fieldKind({ oneOf: [] })).toBe("json");
	});
});

describe("objectFields", () => {
	it("returns ordered fields with $refs resolved + required flags", () => {
		const fields = objectFields(ROOT, ROOT);
		const byName = Object.fromEntries(fields.map((f) => [f.name, f]));
		expect(fields.map((f) => f.name)).toContain("metadata");
		expect(byName.metadata?.required).toBe(true);
		expect(byName.metadata?.schema.type).toBe("object"); // $ref resolved
		expect(byName.category?.required).toBe(false);
	});
});
