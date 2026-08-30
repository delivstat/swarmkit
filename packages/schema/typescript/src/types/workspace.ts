/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * Deployment-level config: identity, governance, model providers, MCP registry, storage.
 * See design §9.3 and design/details/workspace-schema-v1.md.
 */
export interface SwarmKitWorkspace {
    apiVersion: APIVersion;
    /**
     * Local command packs — the sibling of `mcp_servers` for capabilities that already exist as
     * binaries and would otherwise need a wrapper server written for them.
     */
    command_packs?:       CommandPackElement[];
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
 * A named set of local commands exposed to skills through `implementation.type: command`.
 * Governed exactly as an mcp_server is — the pack carries the permission tier, the skill
 * carries `iam.required_scopes`.
 */
export interface CommandPackElement {
    commands:         CommandElement[];
    credentials_ref?: string;
    /**
     * Working directory. Supports ${VAR} expansion. Defaults to workspace root.
     */
    cwd?:         string;
    description?: string;
    /**
     * Environment for every command in the pack. Supports ${VAR} expansion and
     * `{credential.<ref>}` resolved against `credentials_ref`. This is the only place a secret
     * may reach a command.
     */
    env?: { [key: string]: string };
    id:   string;
    /**
     * Combined stdout+stderr ceiling. Exceeding it fails the call rather than truncating, since
     * a truncated result read as complete is the worse outcome.
     */
    max_output_bytes?: number;
    /**
     * Governance permission tier for this pack's commands, matching mcp_server. open: skip
     * governance checks. cautious (default): reads auto-approved, writes go through governance.
     * strict: all calls require explicit approval. readonly: every command declaring `effects:
     * write` is denied.
     */
    permission?: Permission;
    /**
     * Per-command tier overrides. Keys are command ids.
     */
    permission_overrides?: { [key: string]: Permission };
    requires?:             RequireElement[];
    /**
     * Per-command timeout overrides in seconds. Keys are command ids.
     */
    timeout_overrides?: { [key: string]: number };
    /**
     * Pack default. A command with no ceiling can take a run down with it, so an omitted value
     * means the built-in default, never unbounded.
     */
    timeout_seconds?: number;
}

/**
 * A single command in a pack. `argv` is executed directly — there is no shell, so a
 * substituted value can never become an argument or an operator.
 */
export interface CommandElement {
    /**
     * argv, executed with no shell. `{name}` placeholders are filled from the skill's declared
     * inputs and are always passed as exactly one argument, never re-parsed. `{credential.*}`
     * is rejected here — secrets reach a command through the pack's `env` only, so they cannot
     * be model-placed, cannot appear in an audit line, and cannot be read from `ps`.
     */
    argv:         string[];
    description?: string;
    /**
     * Whether this command changes anything. Not inferrable from the binary — `curl` POSTs and
     * `jq` takes `-i` — so the pack author declares it. Undeclared means `write`, so an
     * unclassified command fails closed. `permission: readonly` denies every `write` command.
     */
    effects?: Effects;
    id:       string;
    output?:  Output;
}

/**
 * Whether this command changes anything. Not inferrable from the binary — `curl` POSTs and
 * `jq` takes `-i` — so the pack author declares it. Undeclared means `write`, so an
 * unclassified command fails closed. `permission: readonly` denies every `write` command.
 */
export type Effects = "read" | "write";

export interface Output {
    parse?: Parse;
}

export type Parse = "text" | "json" | "lines";

/**
 * Governance permission tier for this pack's commands, matching mcp_server. open: skip
 * governance checks. cautious (default): reads auto-approved, writes go through governance.
 * strict: all calls require explicit approval. readonly: every command declaring `effects:
 * write` is denied.
 *
 * Governance permission tier for this server's tools. open: skip governance checks.
 * cautious (default): reads auto-approved, writes go through governance. strict: all calls
 * require explicit approval. readonly: write operations denied.
 */
export type Permission = "open" | "cautious" | "strict" | "readonly";

/**
 * A binary this pack needs, checked at workspace load rather than at run time. A topology
 * that only runs where a binary happens to exist is less portable data than one that does
 * not, and the failure should name the binary rather than surface as an exec error mid-run.
 */
export interface RequireElement {
    binary: string;
    /**
     * Constraint such as '>=1.7', compared against the binary's --version output. An
     * unparseable version is a load error, not a silent pass.
     */
    version?: string;
}

/**
 * Opt-in read-side compression of bulk tool/MCP output before it re-enters an agent's
 * context. Off by default. Never applied to the audit log or the inter-agent contract. Env
 * vars SWARMKIT_CONTEXT_COMPRESSION and SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES override the
 * default per deployment. See design/details/context-compression.md.
 */
export interface ContextCompression {
    backend?:       ContextCompressionBackend;
    backend_class?: string;
    min_bytes?:     number;
    /**
     * Per-surface rules matched by tool-name and/or server-id glob. The first override that
     * matches wins; otherwise the top-level backend/min_bytes apply.
     */
    overrides?: OverrideElement[];
}

/**
 * Compression backend. off (default): no compression. columnar: built-in lossless JSON
 * minify + array-of-uniform-dicts rewrite to {columns, rows}. headtail: reversible-lossy —
 * keep head+tail, elide the middle, recallable via the context_retrieve tool (for
 * lossy-tolerant surfaces like logs). plugin: a custom ContextCompressor named by
 * backend_class.
 */
export type ContextCompressionBackend = "off" | "columnar" | "headtail" | "plugin";

/**
 * A per-surface compression rule. At least one of match / match_server is required.
 */
export interface OverrideElement {
    backend?:       ContextCompressionBackend;
    backend_class?: string;
    /**
     * Glob matched against the tool/skill name (e.g. 'get-logs', 'frigate*').
     */
    match?: string;
    /**
     * Glob matched against the MCP server id backing the tool (e.g. 'frigate', 'logs-*').
     */
    match_server?: string;
    min_bytes?:    number;
}

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
     * Whether this binding is active. Set false in a topology to switch off a binding inherited
     * from the workspace. Separate from `required` because the two are different questions —
     * whether a skill runs, and whether its verdict can stop the run.
     */
    enabled?: boolean;
    /**
     * Decision skill ID. Must exist in workspace skill registry.
     */
    id: string;
    /**
     * Whether a failing verdict from this skill STOPS the run. true (the default) means a
     * `fail` rejects the output; false means the skill still runs and its rejection is advisory
     * — logged, not fatal. It does NOT disable the binding: use `enabled: false` for that.
     * Until 1.169.0 a falsey `required` silently discarded the binding, so an advisory skill
     * was never evaluated at all.
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
     * Whether each tool reads or writes, keyed by MCP tool name. Consulted by `permission:
     * readonly`, which allows a tool only when it is known to read. Declared here wins over the
     * server's own `readOnlyHint` annotation, because this is the half the operator controls; a
     * tool with neither is `unknown` and is denied under readonly rather than guessed at. Until
     * 1.199.0 write-ness was inferred by substring-scanning the tool name, which denied
     * `get_dataset` and `read_asset` on 'set' and let `truncate_table` and `purge_cache`
     * through.
     */
    effects?: { [key: string]: Effects };
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
     * Default synthesis/output roles for all topologies: auto-wired to depend on research tasks
     * so they run last. Defaults to ['self', 'document-writer']. Topology-level planning
     * overrides this.
     */
    synthesis_roles?: string[];
    /**
     * Default role name for the automatic synthesis step. Defaults to 'synthesizer'.
     * Topology-level planning overrides this.
     */
    synthesizer_role?: string;
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
    auth?: Auth;
    /**
     * Canary deployment configuration. Routes traffic between topology versions by weight with
     * optional auto-promotion. See design/details/canary-deployments.md.
     */
    canary?: Canary;
    jobs?:   Jobs;
    mcp?:    MCP;
}

/**
 * HTTP authentication for swarmkit serve. Off by default (provider: none). For a
 * non-loopback bind, leaving provider=none refuses to start unless require_on_nonloopback
 * is false (default-secure). See design/details/control-plane/12-auth.md.
 */
export interface Auth {
    config?: Config;
    /**
     * none: open access (default; only safe on loopback). api_key: bearer tokens from a static
     * key registry. jwt: OIDC-compliant JWT bearer tokens (RS256/ES256 + JWKS).
     */
    provider?: AuthProvider;
    /**
     * When true (default), a non-loopback bind (--host other than 127.0.0.1/::1) with
     * provider=none refuses to start. Set false to allow an open non-loopback bind (not
     * recommended).
     */
    require_on_nonloopback?: boolean;
}

/**
 * Provider-specific auth config. keys[] for api_key;
 * issuer/audience/jwks_url/scopes_claim/client_id/scope for jwt; identity/identity_name for
 * none.
 */
export interface Config {
    /**
     * Expected token audience (provider: jwt). Default: swarmkit.
     */
    audience?: string;
    /**
     * The browser's OIDC client registration (provider: jwt), advertised to clients via
     * /auth-info. The server never uses it to validate anything — it advertises it because the
     * portal ships as a pre-built static export, so a build-time env var would be fixed at
     * publish and the same wheel serves every deployment. Without it the portal cannot start a
     * sign-in flow.
     */
    client_id?: string;
    /**
     * Who the caller is asserted to be (provider: none). Default: anonymous. This mode already
     * grants wildcard scopes and authorizes everything, so naming the operator confers no new
     * capability — but the approval engine matches this value against members: in
     * swarm/roles.yaml, so it is what makes a local gate approvable without standing up an
     * identity provider. Asserted, not authenticated: the audit records provider=none.
     */
    identity?: string;
    /**
     * Display name for that identity (provider: none). Defaults to the identity itself.
     */
    identity_name?: string;
    /**
     * OIDC issuer URL (provider: jwt).
     */
    issuer?: string;
    /**
     * JWKS URL (provider: jwt). Defaults to {issuer}/.well-known/jwks.json.
     */
    jwks_url?: string;
    /**
     * API key registry (provider: api_key).
     */
    keys?: KeyElement[];
    /**
     * OIDC scopes the portal requests (provider: jwt), advertised via /auth-info. Default:
     * openid profile email.
     */
    scope?: string;
    /**
     * JWT claim holding scopes (provider: jwt). Default: scope.
     */
    scopes_claim?: string;
}

/**
 * One API key. Grant scopes via a tier (read/run/admin → serve:* scopes) OR explicit scopes
 * — not both.
 */
export interface KeyElement {
    /**
     * Stable id for this caller (appears in audit).
     */
    client_id: string;
    /**
     * Human-readable name. Defaults to client_id.
     */
    client_name?: string;
    /**
     * Reference to the secret, never a literal: 'env:VAR' or a credentials entry. Resolved at
     * startup.
     */
    key_ref: string;
    /**
     * Explicit scopes, as an alternative to tier (e.g. ['serve:read','serve:run']).
     */
    scopes?: string[];
    /**
     * Transport scope tier: read (observe) | run (+ execute) | admin (+ mutate
     * artifacts/rollout). Expands to serve:* scopes.
     */
    tier?: Tier;
}

/**
 * Transport scope tier: read (observe) | run (+ execute) | admin (+ mutate
 * artifacts/rollout). Expands to serve:* scopes.
 */
export type Tier = "read" | "run" | "admin";

/**
 * none: open access (default; only safe on loopback). api_key: bearer tokens from a static
 * key registry. jwt: OIDC-compliant JWT bearer tokens (RS256/ES256 + JWKS).
 */
export type AuthProvider = "none" | "api_key" | "jwt";

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
    /**
     * Where pipeline stage outputs live. Defaults to `database` on the storage.runtime engine.
     * Read by the runtime since the bundled orchestrator shipped, but undeclarable until
     * 1.130.0 — `additionalProperties: false` rejected the very key the code looked for.
     */
    artifacts?: Artifacts;
    /**
     * Append-only audit trail. Inherits storage.runtime unless it overrides it.
     */
    audit?: Audit;
    /**
     * LangGraph run-state checkpointer. The one block that does NOT inherit storage.runtime:
     * postgres here needs `pip install 'swarmkit-runtime[postgres]'`, so it is opt-in rather
     * than implied.
     */
    checkpoints?:     Checkpoints;
    knowledge_bases?: KnowledgeBases;
    /**
     * Backend for jobs, conversations, usage, pipeline sagas, artifacts, fleet enrollment and
     * governed memory. Every other storage block inherits this one unless it overrides it.
     * Defaults to sqlite at .swarmkit/store.sqlite.
     */
    runtime?: Runtime;
}

/**
 * Where pipeline stage outputs live. Defaults to `database` on the storage.runtime engine.
 * Read by the runtime since the bundled orchestrator shipped, but undeclarable until
 * 1.130.0 — `additionalProperties: false` rejected the very key the code looked for.
 */
export interface Artifacts {
    backend?: ArtifactsBackend;
    /**
     * s3 backend: bucket name (requires the boto3 optional dep).
     */
    bucket?: string;
    /**
     * database backend: override the inherited connection URL.
     */
    database_url?: string;
    /**
     * filesystem backend: root directory. Defaults to .swarmkit/artifacts.
     */
    path?: string;
    /**
     * s3 backend: key prefix.
     */
    prefix?: string;
}

export type ArtifactsBackend = "database" | "filesystem" | "s3";

/**
 * Append-only audit trail. Inherits storage.runtime unless it overrides it.
 */
export interface Audit {
    /**
     * Audit backend. Defaults to whatever storage.runtime resolves to. ('agt' was accepted here
     * until 1.130.0, but no writer ever implemented it — selecting it silently wrote sqlite.)
     */
    backend?:        DefaultBackendEnum;
    retention_days?: number;
    /**
     * Connection URL for the postgres backend. Supports ${ENV_VAR} and ${ENV_VAR:-default}
     * interpolation. Optional: inherits storage.runtime.url when omitted.
     */
    url?: string;
}

/**
 * Audit backend. Defaults to whatever storage.runtime resolves to. ('agt' was accepted here
 * until 1.130.0, but no writer ever implemented it — selecting it silently wrote sqlite.)
 *
 * Checkpointer backend. Defaults to sqlite at .swarmkit/state/checkpoints.db.
 *
 * Storage backend. sqlite (default, zero config) or postgres (production, shared).
 */
export type DefaultBackendEnum = "sqlite" | "postgres";

/**
 * LangGraph run-state checkpointer. The one block that does NOT inherit storage.runtime:
 * postgres here needs `pip install 'swarmkit-runtime[postgres]'`, so it is opt-in rather
 * than implied.
 */
export interface Checkpoints {
    /**
     * Checkpointer backend. Defaults to sqlite at .swarmkit/state/checkpoints.db.
     */
    backend?: DefaultBackendEnum;
    path?:    string;
    /**
     * Connection URL for the postgres backend. Supports ${ENV_VAR} and ${ENV_VAR:-default}
     * interpolation. Optional: inherits storage.runtime.url when omitted.
     */
    url?: string;
}

export interface KnowledgeBases {
    default_backend?: DefaultBackendEnum;
}

/**
 * Backend for jobs, conversations, usage, pipeline sagas, artifacts, fleet enrollment and
 * governed memory. Every other storage block inherits this one unless it overrides it.
 * Defaults to sqlite at .swarmkit/store.sqlite.
 */
export interface Runtime {
    /**
     * Storage backend. sqlite (default, zero config) or postgres (production, shared).
     */
    backend?: DefaultBackendEnum;
    /**
     * Connection URL for the postgres backend. Supports ${ENV_VAR} and ${ENV_VAR:-default}
     * interpolation. May also be supplied as SWARMKIT_STORE_URL or DATABASE_URL, which take
     * precedence over this file.
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

