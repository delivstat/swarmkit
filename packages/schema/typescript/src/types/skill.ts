/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * A discrete capability an agent can exercise. See design §6 and
 * design/details/skill-schema-v1.md.
 */
export interface SwarmKitSkill {
    apiVersion: APIVersion;
    /**
     * Runtime semantics differ by category (design §6.2).
     */
    category:       Category;
    constraints?:   Constraints;
    iam?:           Iam;
    implementation: Implementation;
    inputs?:        { [key: string]: FieldSpec };
    kind:           Kind;
    metadata:       Metadata;
    outputs?:       { [key: string]: FieldSpec };
    provenance:     Provenance;
}

export type APIVersion = "swarmkit/v1";

/**
 * Runtime semantics differ by category (design §6.2).
 */
export type Category = "capability" | "decision" | "coordination" | "persistence";

export interface Constraints {
    max_latency_ms?:  number;
    on_failure?:      OnFailure;
    retry?:           Retry;
    timeout_seconds?: number;
}

export type OnFailure = "escalate_to_human" | "fail" | "retry" | "fallback";

export interface Retry {
    attempts?: number;
    backoff?:  Backoff;
}

export type Backoff = "exponential" | "linear" | "none";

export interface Iam {
    required_scopes?: string[];
}

export interface Implementation {
    arguments?: { [key: string]: any };
    server?:    string;
    tool?:      string;
    type:       ImplementationType;
    model?:     { [key: string]: any };
    prompt?:    string;
    composes?:  string[];
    strategy?:  Strategy;
}

export type Strategy = "parallel-consensus" | "sequential" | "custom";

export type ImplementationType = "mcp_tool" | "llm_prompt" | "composed";

/**
 * Required when type=array.
 */
export interface FieldSpec {
    description?: string;
    /**
     * Required when type=array.
     */
    items?: FieldSpec;
    /**
     * Optional when type=object.
     */
    properties?: { [key: string]: FieldSpec };
    /**
     * [min, max] — only for number/integer.
     */
    range?:    number[];
    required?: boolean;
    type:      InputType;
    /**
     * Required when type=enum.
     */
    values?: any[];
}

export type InputType = "string" | "number" | "integer" | "boolean" | "enum" | "object" | "array";

export type Kind = "Skill";

export interface Metadata {
    description: string;
    id:          string;
    name:        string;
}

export interface Provenance {
    authored_by:    AuthoredBy;
    authored_date?: Date;
    registry?:      string;
    vendor?:        string;
    version:        string;
}

export type AuthoredBy = "human" | "authored_by_swarm" | "derived_from_template" | "imported_from_registry" | "vendor_published";

