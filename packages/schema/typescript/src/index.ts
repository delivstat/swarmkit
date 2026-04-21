// SwarmKit schema validators (TypeScript).
//
// The JSON Schema files under `packages/schema/schemas/` are the source of
// truth — this package only validates against them. See
// `packages/schema/CLAUDE.md` for the dual-surface rule.
//
// In dev/CI we import directly from the canonical location (two dirs up).
// For npm publish, `scripts/copy-schemas.mjs` copies the canonical files into
// this package so the tarball is self-contained; the publish-time build
// rewrites imports accordingly. See Milestone 10 of the implementation plan.

import addFormats from "ajv-formats";
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";

import archetypeSchema from "../../schemas/archetype.schema.json" with {
	type: "json",
};
import skillSchema from "../../schemas/skill.schema.json" with { type: "json" };
import topologySchema from "../../schemas/topology.schema.json" with {
	type: "json",
};
import triggerSchema from "../../schemas/trigger.schema.json" with {
	type: "json",
};
import workspaceSchema from "../../schemas/workspace.schema.json" with {
	type: "json",
};

export type SchemaName =
	| "topology"
	| "skill"
	| "archetype"
	| "workspace"
	| "trigger";

const SCHEMAS = {
	topology: topologySchema,
	skill: skillSchema,
	archetype: archetypeSchema,
	workspace: workspaceSchema,
	trigger: triggerSchema,
} as const;

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);

const validators = new Map<SchemaName, ValidateFunction>();

function getValidator(name: SchemaName): ValidateFunction {
	const cached = validators.get(name);
	if (cached) return cached;
	const v = ajv.compile(SCHEMAS[name]);
	validators.set(name, v);
	return v;
}

export function getSchema(name: SchemaName): unknown {
	return SCHEMAS[name];
}

export function validate(
	name: SchemaName,
	instance: unknown,
): { valid: true } | { valid: false; errors: unknown[] } {
	const v = getValidator(name);
	const ok = v(instance);
	return ok ? { valid: true } : { valid: false, errors: v.errors ?? [] };
}

// Re-export generated TypeScript types so consumers can import both the
// validator and typed interfaces from a single module. See
// design/details/ts-codegen.md for the shape-vs-full-validation split
// (same story as pydantic — see design/details/pydantic-codegen.md).
export type {
	SwarmKitArchetype,
	SwarmKitSkill,
	SwarmKitTopology,
	SwarmKitTrigger,
	SwarmKitWorkspace,
} from "./types/index.js";
