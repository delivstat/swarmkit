// Tests for the generated TypeScript types.
//
// TS types are compile-time only, so the tests are a mix of:
//   - import-level checks (types resolve, every root is exported)
//   - assignment checks (a valid fixture, parsed at runtime, assignable to
//     the typed interface after validation)
//
// Same shape-vs-full-validation split as pydantic: these types cover shape
// (required fields, enums, nested structure) but not allOf/if-then rules.
// `validate()` (Ajv + JSON Schema) is authoritative. See
// design/details/ts-codegen.md and design/details/pydantic-codegen.md.

import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { describe, expect, it } from "vitest";

import {
	validate,
	type SchemaName,
	type SwarmKitArchetype,
	type SwarmKitSkill,
	type SwarmKitTopology,
	type SwarmKitTrigger,
	type SwarmKitWorkspace,
} from "../src/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURE_ROOT = resolve(here, "..", "..", "tests", "fixtures");

function validFixtures(kind: string): string[] {
	try {
		return readdirSync(resolve(FIXTURE_ROOT, kind))
			.filter((f) => f.endsWith(".yaml"))
			.sort();
	} catch {
		return [];
	}
}

function load(kind: string, file: string): unknown {
	return parseYaml(readFileSync(resolve(FIXTURE_ROOT, kind, file), "utf-8"));
}

// Compile-time assertions — if these fail, `tsc --noEmit` breaks. The
// runtime body is trivial; the assertions are in the type parameter.
function assertType<T>(_value: T): void {}

describe("generated types — root exports resolve", () => {
	it("exposes all five root types", () => {
		// Each type is used as a type parameter to assertType. If the type
		// isn't exported, tsc fails to compile this file.
		assertType<SwarmKitTopology | undefined>(undefined);
		assertType<SwarmKitSkill | undefined>(undefined);
		assertType<SwarmKitArchetype | undefined>(undefined);
		assertType<SwarmKitWorkspace | undefined>(undefined);
		assertType<SwarmKitTrigger | undefined>(undefined);
	});
});

describe("generated types — fixtures assignable after validation", () => {
	const pairs: Array<
		[kind: SchemaName, typeName: string, file: string]
	> = [];
	for (const kind of [
		"topology",
		"skill",
		"archetype",
		"workspace",
		"trigger",
	] as const) {
		for (const file of validFixtures(kind)) {
			pairs.push([kind, kind, file]);
		}
	}

	for (const [kind, typeName, file] of pairs) {
		it(`${typeName}/${file} is assignable to its root type after validate()`, () => {
			const data = load(kind, file);
			const result = validate(kind, data);
			expect(result.valid).toBe(true);

			// Cast through `unknown` to the typed interface. The cast itself is
			// the assertion: if the generated types' shape disagrees with the
			// schema (e.g. a required field renamed), downstream typed access
			// in this test block would fail compilation.
			switch (kind) {
				case "topology": {
					const typed = data as unknown as SwarmKitTopology;
					expect(typed.apiVersion).toBe("swarmkit/v1");
					expect(typed.kind).toBe("Topology");
					expect(typed.agents.root.id).toBeTypeOf("string");
					break;
				}
				case "skill": {
					const typed = data as unknown as SwarmKitSkill;
					expect(typed.kind).toBe("Skill");
					expect(typed.metadata.id).toBeTypeOf("string");
					expect([
						"capability",
						"decision",
						"coordination",
						"persistence",
					]).toContain(typed.category);
					break;
				}
				case "archetype": {
					const typed = data as unknown as SwarmKitArchetype;
					expect(typed.kind).toBe("Archetype");
					expect(["root", "leader", "worker"]).toContain(typed.role);
					break;
				}
				case "workspace": {
					const typed = data as unknown as SwarmKitWorkspace;
					expect(typed.kind).toBe("Workspace");
					expect(typed.metadata.id).toBeTypeOf("string");
					break;
				}
				case "trigger": {
					const typed = data as unknown as SwarmKitTrigger;
					expect(typed.kind).toBe("Trigger");
					expect(Array.isArray(typed.targets)).toBe(true);
					break;
				}
			}
		});
	}
});
