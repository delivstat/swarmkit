import { describe, expect, it } from "vitest";
import archetypeSchema from "../../schema/schemas/archetype.schema.json";
import skillSchema from "../../schema/schemas/skill.schema.json";
import topologySchema from "../../schema/schemas/topology.schema.json";
import {
	type JsonSchema,
	fieldKind,
	mapValueSchema,
	mergeAllOf,
	normalizeSchema,
	objectFields,
	refType,
	resolveRef,
	variants,
} from "./schema-form";

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
		// nested prompt fields + prose-y names → textarea, not a single-line input
		expect(fieldKind({ type: "string" }, "system")).toBe("text");
		expect(fieldKind({ type: "string" }, "persona")).toBe("text");
		expect(fieldKind({ type: "string" }, "system_prompt")).toBe("text");
		expect(fieldKind({ type: "string" }, "description")).toBe("text");
		// short/id-like names stay single-line
		expect(fieldKind({ type: "string" }, "name")).toBe("string");
		expect(fieldKind({ type: "string" }, "id")).toBe("string");
		expect(fieldKind({ type: "array", items: {} })).toBe("array");
		expect(fieldKind({ type: "object", properties: {} })).toBe("object");
		expect(fieldKind({ oneOf: [] })).toBe("json"); // empty union → JSON
		expect(fieldKind({ oneOf: [{ type: "object" }] })).toBe("oneof");
		expect(
			fieldKind({ type: "object", additionalProperties: { type: "string" } }),
		).toBe("map");
		expect(fieldKind({ allOf: [{ type: "object" }] })).toBe("object");
	});
});

describe("mergeAllOf", () => {
	it("unions properties + required across members", () => {
		const root = {
			$defs: {
				base: { type: "object", properties: { id: {} }, required: ["id"] },
			},
		};
		const merged = mergeAllOf(root, {
			allOf: [
				{ $ref: "#/$defs/base" },
				{ properties: { role: {} }, required: ["role"] },
			],
		});
		expect(Object.keys(merged.properties as object).sort()).toEqual([
			"id",
			"role",
		]);
		expect((merged.required as string[]).sort()).toEqual(["id", "role"]);
	});
});

describe("mapValueSchema", () => {
	it("detects a free-form map (additionalProperties, no fixed properties)", () => {
		expect(
			mapValueSchema({
				type: "object",
				additionalProperties: { type: "string" },
			}),
		).toEqual({
			type: "string",
		});
		expect(
			mapValueSchema({ type: "object", additionalProperties: true }),
		).toEqual({});
	});
	it("is not a map when there are fixed properties or no additionalProperties", () => {
		expect(
			mapValueSchema({
				type: "object",
				properties: { a: {} },
				additionalProperties: true,
			}),
		).toBeNull();
		expect(
			mapValueSchema({ type: "object", additionalProperties: false }),
		).toBeNull();
		expect(mapValueSchema({ type: "string" })).toBeNull();
	});
});

describe("variants", () => {
	it("extracts oneOf variants with a `type` const discriminator", () => {
		const vs = variants(
			{},
			{
				oneOf: [
					{ properties: { type: { const: "mcp_tool" } } },
					{ properties: { type: { const: "llm_prompt" } } },
				],
			},
		);
		expect(vs?.map((v) => v.label)).toEqual(["mcp_tool", "llm_prompt"]);
		expect(vs?.[0]).toMatchObject({
			discriminatorKey: "type",
			discriminatorValue: "mcp_tool",
		});
	});
});

// ---- against the real canonical schemas: every reported field must render, not blob/blank ----

const skill = skillSchema as unknown as JsonSchema;
const archetype = archetypeSchema as unknown as JsonSchema;
const topology = topologySchema as unknown as JsonSchema;

function field(root: JsonSchema, schema: JsonSchema, name: string): JsonSchema {
	const f = objectFields(root, schema).find((x) => x.name === name);
	if (!f) throw new Error(`no field ${name}`);
	return f.schema;
}

describe("real schemas — the reported form fields", () => {
	it("1.4 skill.implementation is a discriminated union, not a blob", () => {
		const impl = field(skill, skill, "implementation");
		expect(fieldKind(impl)).toBe("oneof");
		expect((variants(skill, impl) ?? []).map((v) => v.label).sort()).toEqual([
			"composed",
			"llm_prompt",
			"mcp_tool",
		]);
	});

	it("1.1 archetype.executor.config is a map (was blank)", () => {
		const executor = field(archetype, archetype, "executor");
		expect(fieldKind(executor)).toBe("object"); // has kind/ref/config fields
		const config = field(archetype, executor, "config");
		expect(fieldKind(config)).toBe("map");
	});

	it("1.2 archetype model.options is a map (was no textbox)", () => {
		const defaults = field(archetype, archetype, "defaults");
		const model = field(archetype, defaults, "model");
		expect(fieldKind(field(archetype, model, "options"))).toBe("map");
	});

	it("1.3 skill inputs/outputs are maps (were empty)", () => {
		expect(fieldKind(field(skill, skill, "inputs"))).toBe("map");
		expect(fieldKind(field(skill, skill, "outputs"))).toBe("map");
	});

	it("1.5 topology.agents.root renders as a merged object (allOf), not a blob", () => {
		const agents = field(topology, topology, "agents");
		const root = normalizeSchema(topology, field(topology, agents, "root"));
		expect(fieldKind(root)).toBe("object");
		expect(objectFields(topology, root).map((f) => f.name)).toContain("role");
	});

	it("1.7 governance decision-skill config is a map", () => {
		const gov = field(topology, topology, "governance");
		const skills = field(topology, gov, "decision_skills");
		const item = normalizeSchema(topology, (skills.items ?? {}) as JsonSchema);
		expect(fieldKind(field(topology, item, "config"))).toBe("map");
	});

	it("1.6 knowledge_bases / review_queues rows are editable (map, not empty)", () => {
		const arts = field(topology, topology, "artifacts");
		for (const name of ["knowledge_bases", "review_queues"]) {
			const arr = field(topology, arts, name);
			const item = normalizeSchema(topology, (arr.items ?? {}) as JsonSchema);
			expect(fieldKind(item)).toBe("map");
		}
	});
});

describe("refType", () => {
	it("reads the x-swarmkit-ref hint", () => {
		expect(refType({ "x-swarmkit-ref": "skill" })).toBe("skill");
		expect(refType({ type: "array", "x-swarmkit-ref": "archetype" })).toBe(
			"archetype",
		);
		expect(refType({ type: "string" })).toBeNull();
	});

	it("survives $ref resolution (sibling override)", () => {
		const r = resolveRef(ROOT, {
			$ref: "#/$defs/metadata",
			"x-swarmkit-ref": "archetype",
		});
		expect(refType(r)).toBe("archetype");
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
