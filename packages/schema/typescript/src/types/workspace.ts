/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * Deployment-level config: identity, governance, model providers, MCP registry, storage.
 * See design §9.3 and design/details/workspace-schema-v1.md.
 */
export interface SwarmKitWorkspace {
    apiVersion:           APIVersion;
    context_compression?: ContextCompression;
    credentials?:         { [key: string]: CredentialValue };
    governance?:          Governance;
    identity?:            Identity;
    kind:                 Kind;
    mcp_servers?:         MCPServerElement[];
    metadata:             Metadata;
    model_providers?:     ModelProviderElement[];
    organisation?:        Organisation;
    planning?:            Planning;
    server?:              Server;
    storage?:             Storage;
    synthesis?:           Synthesis;
    team?:                Organisation;
}

export type APIVersion = "swarmkit/v1";

/**
 * Opt-in read-side compression of bulk tool/MCP output before it re-enters an agent's
 * context. Off by default. Never applied to the audit log or the inter-agent contract. Env
 * vars SWARMKIT_CONTEXT_COMPRESSION and SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES override
 * these values per deployment. See design/details/context-compression.md.
 */
export interface ContextCompression {
    /**
     * Compression backend. off (default): no compression. columnar: built-in lossless JSON
     * minify + array-of-uniform-dicts rewrite to {columns, rows}.
     */
    backend?: ContextCompressionBackend;
    /**
     * Payloads smaller than this (in characters) are left untouched — avoids columnar overhead
     * on small results.
     */
    min_bytes?: number;
}

/**
 * Compression backend. off (default): no compression. columnar: built-in lossless JSON
 * minify + array-of-uniform-dicts rewrite to {columns, rows}.
 */
export type ContextCompressionBackend = "off" | "columnar";

export interface CredentialValue {
    /**
     * Provider-specific configuration. Runtime validates shape per source.
     */
    config: { [key: string]: any };
    /**
     * Required when source=plugin. Names the registered SecretsProvider.
     */
    provider_id?: string;
    source:       Source;
}

export type Source = "env" | "file" | "hashicorp-vault" | "aws-secrets-manager" | "gcp-secret-manager" | "azure-key-vault" | "plugin";

export interface Governance {
    config?: { [key: string]: any };
    /**
     * Mandatory decision skills that fire at specified trigger points. Topologies inherit these
     * and can override by id.
     */
    decision_skills?: DecisionSkillElement[];
    /**
     * Circuit breaker thresholds. Prevents runaway execution and cost overruns.
     */
    limits?: Limits;
    /**
     * §21 open question — default yaml for v1.0.
     */
    policy_language?: PolicyLanguage;
    /**
     * GovernanceProvider implementation (design §8.5).
     */
    provider: GovernanceProvider;
}

/**
 * Binds a decision skill to a trigger point. Workspace bindings are inherited by all
 * topologies; topology bindings can override by id.
 */
export interface DecisionSkillElement {
    /**
     * Skill-specific configuration (confidence thresholds, retry limits, etc).
     */
    config?: { [key: string]: any };
    /**
     * Decision skill ID. Must exist in workspace skill registry.
     */
    id: string;
    /**
     * If true, output is rejected when this skill returns a failing verdict. Set false to
     * disable an inherited workspace binding.
     */
    required?: boolean;
    /**
     * Comma-separated agent IDs this binding applies to. Default '*' = all agents in the
     * topology.
     */
    scope?: string;
    /**
     * When the skill fires: pre_input (before agent runs, validates user input), post_output
     * (after agent output), checkpoint (between task batches), pre_synthesis (before final
     * synthesis).
     */
    trigger: Trigger;
}

/**
 * When the skill fires: pre_input (before agent runs, validates user input), post_output
 * (after agent output), checkpoint (between task batches), pre_synthesis (before final
 * synthesis).
 */
export type Trigger = "pre_input" | "post_output" | "checkpoint" | "pre_synthesis";

/**
 * Circuit breaker thresholds. Prevents runaway execution and cost overruns.
 */
export interface Limits {
    /**
     * Maximum estimated LLM cost (USD) per run before abort.
     */
    max_cost_per_run_usd?: number;
    /**
     * Maximum execution steps per individual agent before abort.
     */
    max_steps_per_agent?: number;
    /**
     * Maximum total steps across all agents in a single run. Default: 500.
     */
    max_steps_per_run?: number;
}

/**
 * §21 open question — default yaml for v1.0.
 */
export type PolicyLanguage = "yaml" | "rego" | "cedar";

/**
 * GovernanceProvider implementation (design §8.5).
 */
export type GovernanceProvider = "agt" | "mock" | "custom";

export interface Identity {
    config?: { [key: string]: any };
    /**
     * Human-identity provider (design §16.1).
     */
    provider: IdentityProvider;
}

/**
 * Human-identity provider (design §16.1).
 */
export type IdentityProvider = "builtin" | "auth0" | "okta" | "google" | "azure-ad" | "oidc";

export type Kind = "Workspace";

export interface MCPServerElement {
    /**
     * Required when transport=stdio.
     */
    command?:         string[];
    credentials_ref?: string;
    /**
     * Working directory for stdio servers. Supports ${VAR} expansion. Defaults to workspace
     * root.
     */
    cwd?: string;
    /**
     * Required when transport=http.
     */
    endpoint?: string;
    /**
     * Environment variables passed to a stdio server. Values support ${VAR} expansion from the
     * runtime process environment. Use `credentials_ref` for secrets; `env` is for
     * configuration.
     */
    env?: { [key: string]: string };
    id:   string;
    /**
     * Governance permission tier for this server's tools. open: skip governance checks.
     * cautious (default): reads auto-approved, writes go through governance. strict: all calls
     * require explicit approval. readonly: write operations denied.
     */
    permission?: Permission;
    /**
     * Per-tool permission overrides. Keys are MCP tool names, values are permission tiers that
     * override the server default.
     */
    permission_overrides?: { [key: string]: Permission };
    /**
     * Docker image for sandboxed servers. Defaults to swarmkit-mcp-sandbox (Python + mcp SDK).
     * Use node:22-slim for Node.js servers.
     */
    sandbox_image?: string;
    /**
     * True forces Docker-or-equivalent isolation (design §8.8).
     */
    sandboxed?: boolean;
    transport:  Transport;
}

/**
 * Governance permission tier for this server's tools. open: skip governance checks.
 * cautious (default): reads auto-approved, writes go through governance. strict: all calls
 * require explicit approval. readonly: write operations denied.
 */
export type Permission = "open" | "cautious" | "strict" | "readonly";

export type Transport = "stdio" | "http";

export interface Metadata {
    /**
     * Arbitrary key-value metadata the runtime ignores. For enterprise use: cost_center, team,
     * environment, compliance tags, etc.
     */
    annotations?: { [key: string]: string };
    description?: string;
    id:           string;
    name:         string;
}

export interface ModelProviderElement {
    /**
     * Fully-qualified Python class path.
     */
    class:       string;
    config?:     { [key: string]: any };
    provider_id: string;
}

export interface Organisation {
    id:    string;
    name?: string;
}

/**
 * Default planning behavior for all topologies in this workspace. Topology-level planning
 * overrides these defaults.
 */
export interface Planning {
    /**
     * Leaders must call create-scope before synthesis.
     */
    scope_required?: boolean;
    /**
     * Enforce two-phase planning for all topologies.
     */
    two_phase?: boolean;
}

/**
 * Configuration for swarmkit serve mode. Controls job concurrency, timeouts, MCP server
 * lifecycle, and canary deployments.
 */
export interface Server {
    /**
     * Canary deployment configuration. Routes traffic between topology versions by weight with
     * optional auto-promotion. See design/details/canary-deployments.md.
     */
    canary?: Canary;
    jobs?:   Jobs;
    mcp?:    MCP;
}

/**
 * Canary deployment configuration. Routes traffic between topology versions by weight with
 * optional auto-promotion. See design/details/canary-deployments.md.
 */
export interface Canary {
    /**
     * Canary routes. Each route splits traffic for one topology across multiple versions.
     */
    routes?: RouteElement[];
}

/**
 * Traffic splitting rule for a single topology.
 */
export interface RouteElement {
    /**
     * Topology name (matches metadata.name). Must exist in the workspace.
     */
    topology: string;
    /**
     * Version entries. Weights must sum to 100.
     */
    versions: VersionElement[];
}

/**
 * A single version in a canary route with its traffic weight and optional promotion
 * criteria.
 */
export interface VersionElement {
    /**
     * Auto-promotion criteria. When all conditions are met, this version is promoted to 100%
     * traffic.
     */
    promote_when?: PromoteWhen;
    /**
     * Topology version (semver). Must match a topology file's metadata.version.
     */
    version: string;
    /**
     * Percentage of traffic routed to this version (0-100).
     */
    weight: number;
}

/**
 * Auto-promotion criteria. When all conditions are met, this version is promoted to 100%
 * traffic.
 *
 * Conditions that must ALL be met for auto-promotion of a canary version.
 */
export interface PromoteWhen {
    /**
     * Maximum average drift score. E.g. 0.30 = low drift tolerance.
     */
    drift_below?: number;
    /**
     * Maximum error rate (failed/total). E.g. 0.05 = 5% error rate threshold.
     */
    error_rate_below?: number;
    /**
     * Minimum number of completed runs before promotion is eligible.
     */
    min_runs?: number;
    /**
     * Evaluation window in minutes. Only runs within this window count toward promotion
     * criteria.
     */
    window_minutes?: number;
}

export interface Jobs {
    /**
     * Maximum number of concurrent topology executions.
     */
    max_concurrent?: number;
    /**
     * Per-job execution timeout in seconds.
     */
    timeout_seconds?: number;
}

export interface MCP {
    /**
     * Whether to start MCP servers at boot in serve mode.
     */
    enabled?: boolean;
}

export interface Storage {
    audit?:           Audit;
    checkpoints?:     Checkpoints;
    knowledge_bases?: KnowledgeBases;
    /**
     * Backend for jobs, conversations, and usage tracking. Defaults to sqlite at
     * .swarmkit/store.sqlite.
     */
    runtime?: Runtime;
}

export interface Audit {
    backend?:        AuditBackend;
    retention_days?: number;
    url?:            string;
}

export type AuditBackend = "agt" | "sqlite" | "postgres";

export interface Checkpoints {
    backend?: DefaultBackendEnum;
    path?:    string;
    url?:     string;
}

/**
 * Storage backend. sqlite (default, zero config) or postgres (production, shared).
 */
export type DefaultBackendEnum = "sqlite" | "postgres";

export interface KnowledgeBases {
    default_backend?: DefaultBackendEnum;
}

/**
 * Backend for jobs, conversations, and usage tracking. Defaults to sqlite at
 * .swarmkit/store.sqlite.
 */
export interface Runtime {
    /**
     * Storage backend. sqlite (default, zero config) or postgres (production, shared).
     */
    backend?: DefaultBackendEnum;
    /**
     * Connection URL for postgres backend. Supports ${ENV_VAR} interpolation. Ignored for
     * sqlite.
     */
    url?: string;
}

/**
 * Automatic synthesis config. When set, the compiler invokes a large-context model directly
 * with all research results instead of having the architect write the document.
 */
export interface Synthesis {
    /**
     * Model name for synthesis (e.g. gemini-2.5-flash).
     */
    model: string;
    /**
     * Custom system prompt for the synthesizer. Overrides the platform default. Use this to
     * control document style, diagram generation (mermaid), grounding rules, and section
     * handling.
     */
    prompt?: string;
    /**
     * Model provider ID (e.g. google, openrouter, anthropic).
     */
    provider: string;
}

