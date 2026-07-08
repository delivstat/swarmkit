import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
	type ProtocolSchemaName,
	getProtocolSchema,
	validateProtocol,
} from "../src/index.js";

const here = dirname(fileURLToPath(import.meta.url));
// Shared with the Python validator — both languages round-trip the same fixtures (dual-surface rule).
const FIXTURE_ROOT = resolve(here, "..", "..", "tests", "fixtures", "protocol");

const ALL: ProtocolSchemaName[] = [
	"credential",
	"instance-state",
	"register-request",
	"register-response",
	"join-request",
	"join-response",
];

function fixtures(kind: string): string[] {
	try {
		return readdirSync(resolve(FIXTURE_ROOT, kind))
			.filter((f) => f.endsWith(".json"))
			.sort();
	} catch {
		return [];
	}
}

function loadJson(kind: string, file: string): unknown {
	return JSON.parse(readFileSync(resolve(FIXTURE_ROOT, kind, file), "utf-8"));
}

describe("swarmkit-schema · protocol", () => {
	for (const name of ALL) {
		it(`exposes the ${name} schema`, () => {
			expect(getProtocolSchema(name)).toBeTypeOf("object");
		});
	}

	for (const name of ALL) {
		const valid = fixtures(name);
		it(`has at least one valid + invalid fixture for ${name}`, () => {
			expect(valid.length).toBeGreaterThan(0);
			expect(fixtures(`${name}-invalid`).length).toBeGreaterThan(0);
		});
		for (const file of valid) {
			it(`accepts ${name}/${file}`, () => {
				const res = validateProtocol(name, loadJson(name, file));
				expect(res.valid, JSON.stringify("errors" in res && res.errors)).toBe(
					true,
				);
			});
		}
		for (const file of fixtures(`${name}-invalid`)) {
			it(`rejects ${name}-invalid/${file}`, () => {
				expect(
					validateProtocol(name, loadJson(`${name}-invalid`, file)).valid,
				).toBe(false);
			});
		}
	}

	it("enforces the embedded instance_state via cross-file $ref", () => {
		const resp = loadJson("register-response", "valid.json") as Record<
			string,
			Record<string, unknown>
		>;
		// biome-ignore lint/performance/noDelete: mutating a test fixture clone, not hot-path
		delete resp.instance_state.artifacts;
		expect(validateProtocol("register-response", resp).valid).toBe(false);
	});
});
