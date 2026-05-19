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
    audit?:     Audit;
    /**
     * Runtime semantics differ by category (design §6.2).
     */
    category:       Category;
    constraints?:   Constraints;
    iam?:           Iam;
    implementation: Implementation;
    /**
     * JSON Schema (draft 2020-12) defining the skill's input shape.
     */
    inputs?:  { [key: string]: any };
    kind:     Kind;
    metadata: Metadata;
    /**
     * JSON Schema defining the skill's output shape. Passed to providers for structured
     * generation (Tier 0) and used for deterministic validation (Tier 1). See
     * design/details/structured-output-governance.md.
     */
    outputs?:   { [key: string]: any };
    provenance: Provenance;
}

export type APIVersion = "swarmkit/v1";

/**
 * Controls what gets logged when this skill executes. Per-skill privacy/compliance control.
 */
export interface Audit {
    /**
     * How much of the skill's input to log. Default varies by category: decision=full,
     * capability=summary.
     */
    log_inputs?: LogPuts;
    /**
     * How much of the skill's output to log.
     */
    log_outputs?: LogPuts;
    /**
     * JSON paths to redact from logged inputs/outputs (e.g. '$.password', '$.api_key').
     */
    redact?: string[];
}

/**
 * How much of the skill's input to log. Default varies by category: decision=full,
 * capability=summary.
 *
 * How much of the skill's output to log.
 */
export type LogPuts = "full" | "summary" | "none";

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
    type:       Type;
    model?:     { [key: string]: any };
    prompt?:    string;
    composes?:  string[];
    strategy?:  Strategy;
}

export type Strategy = "parallel-consensus" | "sequential" | "custom";

export type Type = "mcp_tool" | "llm_prompt" | "composed";

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

