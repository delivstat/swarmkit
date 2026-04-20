// SwarmKit schema validators (TypeScript).
//
// The JSON Schema files under `../schemas/` are the source of truth — this
// package only validates against them. See `packages/schema/CLAUDE.md` for the
// dual-surface rule.

import Ajv, { type ValidateFunction } from "ajv";
import addFormats from "ajv-formats";

import archetypeSchema from "../schemas/archetype.schema.json" with { type: "json" };
import skillSchema from "../schemas/skill.schema.json" with { type: "json" };
import topologySchema from "../schemas/topology.schema.json" with { type: "json" };
import triggerSchema from "../schemas/trigger.schema.json" with { type: "json" };
import workspaceSchema from "../schemas/workspace.schema.json" with { type: "json" };

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

const ajv = new Ajv({ strict: false, allErrors: true });
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
