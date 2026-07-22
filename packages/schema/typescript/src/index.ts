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

import approvalPolicySchema from "../../schemas/approval-policy.schema.json" with {
	type: "json",
};
import archetypeSchema from "../../schemas/archetype.schema.json" with {
	type: "json",
};
import contractSchema from "../../schemas/contract.schema.json" with {
	type: "json",
};
import executorAdapterSchema from "../../schemas/executor-adapter.schema.json" with {
	type: "json",
};
import funnelSchema from "../../schemas/funnel.schema.json" with {
	type: "json",
};
import credentialSchema from "../../schemas/protocol/credential.schema.json" with {
	type: "json",
};
import fleetIdentitySchema from "../../schemas/protocol/fleet-identity.schema.json" with {
	type: "json",
};
import instanceStateSchema from "../../schemas/protocol/instance-state.schema.json" with {
	type: "json",
};
import joinRequestSchema from "../../schemas/protocol/join-request.schema.json" with {
	type: "json",
};
import joinResponseSchema from "../../schemas/protocol/join-response.schema.json" with {
	type: "json",
};
import registerRequestSchema from "../../schemas/protocol/register-request.schema.json" with {
	type: "json",
};
import registerResponseSchema from "../../schemas/protocol/register-response.schema.json" with {
	type: "json",
};
import roleRegistrySchema from "../../schemas/role-registry.schema.json" with {
	type: "json",
};
import skillSchema from "../../schemas/skill.schema.json" with { type: "json" };
import stageGraphSchema from "../../schemas/stage-graph.schema.json" with {
	type: "json",
};
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
	| "trigger"
	| "executor-adapter"
	| "role-registry"
	| "approval-policy"
	| "funnel"
	| "stage-graph"
	| "contract";

const SCHEMAS = {
	topology: topologySchema,
	skill: skillSchema,
	archetype: archetypeSchema,
	workspace: workspaceSchema,
	trigger: triggerSchema,
	"executor-adapter": executorAdapterSchema,
	"role-registry": roleRegistrySchema,
	"approval-policy": approvalPolicySchema,
	funnel: funnelSchema,
	"stage-graph": stageGraphSchema,
	contract: contractSchema,
} as const;

// Fleet-enrollment wire schemas (design details/control-plane/19-fleet-enrollment-protocol.md).
// Distinct from the artifact schemas: API request/response contracts, not user-authored artifacts.
export type ProtocolSchemaName =
	| "credential"
	| "fleet-identity"
	| "instance-state"
	| "register-request"
	| "register-response"
	| "join-request"
	| "join-response";

const PROTOCOL_SCHEMAS = {
	credential: credentialSchema,
	"fleet-identity": fleetIdentitySchema,
	"instance-state": instanceStateSchema,
	"register-request": registerRequestSchema,
	"register-response": registerResponseSchema,
	"join-request": joinRequestSchema,
	"join-response": joinResponseSchema,
} as const;

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);

// All protocol schemas share one Ajv so the responses' `$ref`s (by `$id`) resolve across files.
const protocolAjv = new Ajv2020({ strict: false, allErrors: true });
addFormats(protocolAjv);
for (const schema of Object.values(PROTOCOL_SCHEMAS)) {
	protocolAjv.addSchema(schema);
}

const validators = new Map<SchemaName, ValidateFunction>();

function getValidator(name: SchemaName): ValidateFunction {
	const cached = validators.get(name);
	if (cached) return cached;
	const v = ajv.compile(SCHEMAS[name]);
	validators.set(name, v);
	return v;
}

function getProtocolValidator(name: ProtocolSchemaName): ValidateFunction {
	const schema = PROTOCOL_SCHEMAS[name] as { $id: string };
	const existing = protocolAjv.getSchema(schema.$id);
	if (existing) return existing as ValidateFunction;
	return protocolAjv.compile(PROTOCOL_SCHEMAS[name]);
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

export function getProtocolSchema(name: ProtocolSchemaName): unknown {
	return PROTOCOL_SCHEMAS[name];
}

// Validate a fleet-enrollment message (register/join/InstanceState/credential, design 19).
// Cross-file `$ref`s resolve because every protocol schema is registered in one Ajv.
export function validateProtocol(
	name: ProtocolSchemaName,
	instance: unknown,
): { valid: true } | { valid: false; errors: unknown[] } {
	const v = getProtocolValidator(name);
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
