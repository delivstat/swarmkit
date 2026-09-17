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
    command?:   string;
    pack?:      string;
    /**
     * A remote agent's Agent Card URL (usually .../.well-known/agent-card.json). The card's
     * `url` is where the task API is called.
     */
    card_url?: string;
    /**
     * Remote only: a workspace `credentials` entry, resolved by the credential service and sent
     * as a bearer token.
     */
    credentials_ref?: string;
    /**
     * What calling this agent does to the world. Under `readonly`, only `read` is allowed —
     * `unknown` is denied, fail-closed, as for MCP tools.
     */
    effects?: Effects;
    /**
     * Under `agent`: how many questions the calling agent may answer on one task before the
     * next one is relayed to a person.
     */
    max_agent_answers?: number;
    /**
     * What happens when the other agent asks a question — the same words a harness adapter uses
     * for a mid-run request. relay: a person answers through the review queue (bounded wait,
     * never hangs). abort: the call fails with the question as the reason. agent: the question
     * is the tool result and the calling agent answers it, bounded by max_agent_answers and
     * audited; past the budget it relays. A human gate on the other side is never the agent's
     * to resolve, whatever this says.
     */
    on_unanswerable?: OnUnanswerable;
    /**
     * Governance tier for the call, as an MCP server's `permission` is for its tools.
     */
    permission?: Permission;
    /**
     * Remote only: which of the card's skills this skill invokes (sent as
     * message.metadata.skill). Omitted: the card's first skill.
     */
    skill_id?: string;
    /**
     * How long to wait for the other agent's task to finish before the call fails.
     */
    timeout_s?: number;
    /**
     * A topology in this workspace. Runs as a child job of the caller's run (same correlation,
     * parent_job_id set); no network.
     */
    topology?: string;
}

/**
 * What calling this agent does to the world. Under `readonly`, only `read` is allowed —
 * `unknown` is denied, fail-closed, as for MCP tools.
 */
export type Effects = "read" | "write" | "unknown";

/**
 * What happens when the other agent asks a question — the same words a harness adapter uses
 * for a mid-run request. relay: a person answers through the review queue (bounded wait,
 * never hangs). abort: the call fails with the question as the reason. agent: the question
 * is the tool result and the calling agent answers it, bounded by max_agent_answers and
 * audited; past the budget it relays. A human gate on the other side is never the agent's
 * to resolve, whatever this says.
 */
export type OnUnanswerable = "agent" | "relay" | "abort";

/**
 * Governance tier for the call, as an MCP server's `permission` is for its tools.
 */
export type Permission = "open" | "cautious" | "strict" | "readonly";

export type Strategy = "parallel-consensus" | "sequential" | "custom";

export type Type = "mcp_tool" | "llm_prompt" | "composed" | "command" | "agent";

export type Kind = "Skill";

export interface Metadata {
    description: string;
    id:          string;
    name:        string;
}

export interface Provenance {
    authored_by:       AuthoredBy;
    authored_date?:    Date;
    registry?:         string;
    requires_runtime?: string;
    vendor?:           string;
    version:           string;
}

export type AuthoredBy = "human" | "authored_by_swarm" | "derived_from_template" | "imported_from_registry" | "vendor_published";

