import { readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { describe, expect, it } from "vitest";

import { getSchema, validate, type SchemaName } from "../src/index.js";

const here = dirname(fileURLToPath(import.meta.url));
// Fixtures live at packages/schema/tests/fixtures — shared with the Python
// validator so both languages round-trip the same artifacts.
const FIXTURE_ROOT = resolve(here, "..", "..", "tests", "fixtures");

const ALL_SCHEMAS: SchemaName[] = [
	"topology",
	"skill",
	"archetype",
	"workspace",
	"trigger",
];

function fixtures(kind: string): string[] {
	try {
		return readdirSync(resolve(FIXTURE_ROOT, kind))
			.filter((f) => f.endsWith(".yaml"))
			.sort();
	} catch {
		return [];
	}
}

function loadYaml(kind: string, file: string): unknown {
	return parseYaml(
		readFileSync(resolve(FIXTURE_ROOT, kind, file), "utf-8"),
	);
}

describe("swarmkit-schema", () => {
	for (const name of ALL_SCHEMAS) {
		it(`exposes ${name} schema`, () => {
			expect(getSchema(name)).toBeTypeOf("object");
		});
	}
});

function describeFixtures(
	label: string,
	validDir: string,
	invalidDir: string,
	schemaName: SchemaName,
) {
	describe(`${label} fixtures`, () => {
		for (const file of fixtures(validDir)) {
			it(`accepts ${file}`, () => {
				const result = validate(schemaName, loadYaml(validDir, file));
				if (!result.valid) {
					throw new Error(
						`validation failed: ${JSON.stringify(result.errors)}`,
					);
				}
			});
		}
		for (const file of fixtures(invalidDir)) {
			it(`rejects ${file}`, () => {
				const result = validate(schemaName, loadYaml(invalidDir, file));
				expect(result.valid).toBe(false);
			});
		}
	});
}

describeFixtures("topology", "topology", "topology-invalid", "topology");
describeFixtures("skill", "skill", "skill-invalid", "skill");
describeFixtures("archetype", "archetype", "archetype-invalid", "archetype");
describeFixtures("workspace", "workspace", "workspace-invalid", "workspace");
