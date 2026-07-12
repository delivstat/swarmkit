/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * A declarative harness adapter (design/details/executor-declarative-adapters-plan.md).
 * Defines how to launch an external agentic harness as a subprocess and map its
 * line-delimited JSON output into the normalized ExecEvent vocabulary — so a new harness is
 * added as data (this artifact), with no Python and no runtime release. The DSL is
 * deliberately minimal (RFC executor-abstraction.md decision 1a): JSONL streams only,
 * literal-equality matching, dotted-path/for_each extraction, a named map for enum
 * translation; anything past that ceiling declares `requires: code` and graduates to a
 * Tier-1 Python Executor.
 */
export interface SwarmKitExecutorAdapter {
    apiVersion: APIVersion;
    kind:       SwarmKitExecutorAdapterKind;
    metadata:   Metadata;
    provenance: Provenance;
    spec:       Spec;
}

export type APIVersion = "swarmkit/v1";

export type SwarmKitExecutorAdapterKind = "ExecutorAdapter";

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

export interface Spec {
    artifacts?: Artifacts;
    auth?:      Auth;
    event_map:  EventMapElement[];
    grant?:     Grant;
    /**
     * Mid-run interaction config, required when `on_unanswerable` is `relay` (RFC §6.2).
     * Declares the bidirectional driver that feeds an approval decision back into the running
     * session — the single per-harness code seam; everything else (inbox, policy, scoping,
     * audit, never-hang) is generic core.
     */
    interaction?: Interaction;
    launch:       Launch;
    /**
     * How a mid-run request outside the launch grant is handled (RFC §6.2). `deny` refuses it
     * in place; `abort` terminates needs_approval; `relay` pauses the harness and routes the
     * request to the approval inbox, then feeds the decision back. `relay` requires an
     * `interaction` block with a driver (the one Tier-1 seam — bidirectional session control).
     */
    on_unanswerable?: OnUnanswerable;
    /**
     * Present only when this adapter has hit the declarative DSL ceiling and must be
     * implemented as a Tier-1 Python Executor instead. A validator/loader treats `requires:
     * code` as 'not runnable declaratively'.
     */
    requires?: Requires;
    resume?:   Resume;
    sandbox?:  Sandbox;
    /**
     * Vendor discriminator value -> ExecResultStatus
     * (success|failure|budget_exceeded|cancelled|needs_approval|stalled). `_default` covers the
     * rest.
     */
    status_map?: { [key: string]: string };
    stream:      Stream;
    /**
     * Terminal success predicate. Core layers its semantic check (typed output +
     * artifact-manifest match) on top — exit code alone is necessary, not sufficient.
     */
    success_when?: SuccessWhen;
    /**
     * `opaque` (unobservable) adapters are denied by default (RFC decision 5); use requires
     * explicit per-archetype opt-in.
     */
    telemetry_grade?: TelemetryGrade;
}

export interface Artifacts {
    profile?: Profile;
}

export type Profile = "files" | "structured" | "media";

/**
 * Which authentication modes the harness supports (RFC decision 4), expressed generically.
 * A mode contributes to the launch however the harness needs — env vars, extra command
 * args, and/or provisioned credential paths — so auth may be an env var, a command flag,
 * saved CLI credentials, or any combination; the engine has no per-mode special-casing.
 * Both modes may be declared; `default` sets the mode used when the workspace/archetype
 * does not override. In headless mode api_key takes precedence over subscription where both
 * are usable.
 */
export interface Auth {
    /**
     * The auth mode used when the workspace/archetype does not override.
     */
    default?: Default;
    /**
     * The auth modes this adapter can run under, keyed by mode name; each value declares that
     * mode's contribution to the launch.
     */
    modes?: Modes;
}

/**
 * The auth mode used when the workspace/archetype does not override.
 */
export type Default = "api_key" | "subscription";

/**
 * The auth modes this adapter can run under, keyed by mode name; each value declares that
 * mode's contribution to the launch.
 */
export interface Modes {
    api_key?:      APIKey;
    subscription?: APIKey;
}

/**
 * What an auth mode contributes to the launch. All parts optional — combine as the harness
 * requires. Env and args are merged into the launch command/environment when this mode is
 * active; credential_paths are provisioned into the sandbox.
 */
export interface APIKey {
    /**
     * Command args this mode appends, e.g. [--api-key, "{credential.model_provider}"] — for
     * harnesses that take the key on the command line rather than the environment.
     */
    args?: string[];
    /**
     * Paths (files/dirs) holding the vendor's saved auth state to provision into the sandbox,
     * e.g. ~/.claude — for subscription/CLI-login modes.
     */
    credential_paths?: string[];
    /**
     * Env vars this mode injects, e.g. {"ANTHROPIC_API_KEY": "{credential.model_provider}"}.
     */
    env?: { [key: string]: string };
}

/**
 * One event-map rule: match a parsed JSON line, optionally iterate an array, capture state,
 * and/or emit ExecEvents.
 */
export interface EventMapElement {
    emit?: EmitElement[];
    /**
     * A dotted path to an array; the rule's `emit` runs once per item, with `$.` resolving
     * against the item.
     */
    for_each?: string;
    /**
     * Capture values into adapter state (no event). The only recognized key is `session_id`
     * (used for resume tokens).
     */
    set?: { [key: string]: SetValue };
    /**
     * Literal-equality match on dotted field paths of the parsed line (keys are dotted paths,
     * values are the required literals). Empty/absent = matches every line.
     */
    when?: { [key: string]: SetValue };
}

/**
 * Emit one ExecEvent. `with` maps event fields to extracted values or literals.
 */
export interface EmitElement {
    event: Event;
    /**
     * Optional per-item literal match (used inside a `for_each` to select array items by type).
     */
    when?: { [key: string]: SetValue };
    /**
     * Event field -> extraction path ($.a.b) or literal. A field whose value is {"from": "$.x",
     * "map": "status_map"} is translated through the named map (the only enum-translation
     * primitive).
     */
    with?: { [key: string]: WithValue };
}

export type Event = "started" | "message" | "tool_call" | "artifact" | "usage" | "approval_requested" | "input_requested" | "result" | "raw";

/**
 * A value in an event `with`/`set` block. A string starting with `$.` is a dotted-path
 * extraction from the parsed JSON line (with array indexing; iterate arrays via a rule's
 * `for_each`); any other scalar is a literal.
 */
export type SetValue = boolean | number | null | string;

export type WithValue = boolean | With | number | null | string;

export interface With {
    from: string;
    map:  string;
}

/**
 * How an approved capability is passed to the harness on a park-resume relaunch (RFC §6.2).
 * The whole park-resume mechanism is data: no harness is special-cased in code.
 */
export interface Grant {
    /**
     * Args appended on a grant-expanding resume, e.g. [--allowedTools, "{grant.capabilities}"].
     * {grant.capabilities} is the approved capabilities joined by `separator`.
     */
    arg?: string[];
    /**
     * How multiple approved capabilities are joined into {grant.capabilities}.
     */
    separator?: string;
}

/**
 * Mid-run interaction config, required when `on_unanswerable` is `relay` (RFC §6.2).
 * Declares the bidirectional driver that feeds an approval decision back into the running
 * session — the single per-harness code seam; everything else (inbox, policy, scoping,
 * audit, never-hang) is generic core.
 */
export interface Interaction {
    /**
     * `hold-stream` keeps the session alive and answers over streaming stdin (short waits);
     * `park-resume` checkpoints the session id and re-launches with an expanded grant on
     * approval (long waits, survives restarts).
     */
    driver: Driver;
    /**
     * Bounded wait for an approval decision before degrading to `abort` (never-hang guarantee).
     * Core applies a default when omitted.
     */
    max_approval_wait_seconds?: number;
}

/**
 * `hold-stream` keeps the session alive and answers over streaming stdin (short waits);
 * `park-resume` checkpoints the session id and re-launches with an expanded grant on
 * approval (long waits, survives restarts).
 */
export type Driver = "hold-stream" | "park-resume";

/**
 * How to launch the harness subprocess. `command` is argv (no shell); substitution is
 * value-only.
 */
export interface Launch {
    command: string[];
    /**
     * Environment variables injected at launch. The only allowed secret is the model-provider
     * credential (RFC §7): {credential.model_provider}. All other secrets are proxy-injected
     * only (not here).
     */
    env?: { [key: string]: string };
    /**
     * Arg groups appended only when the referenced variable is set; the whole group drops when
     * it is empty.
     */
    optional_args?: OptionalArg[];
}

export interface OptionalArg {
    args: string[];
    /**
     * A substitution variable name (without braces), e.g. `budget.max_turns` or `config.model`.
     */
    when: string;
}

/**
 * How a mid-run request outside the launch grant is handled (RFC §6.2). `deny` refuses it
 * in place; `abort` terminates needs_approval; `relay` pauses the harness and routes the
 * request to the approval inbox, then feeds the decision back. `relay` requires an
 * `interaction` block with a driver (the one Tier-1 seam — bidirectional session control).
 */
export type OnUnanswerable = "deny" | "abort" | "relay";

/**
 * Present only when this adapter has hit the declarative DSL ceiling and must be
 * implemented as a Tier-1 Python Executor instead. A validator/loader treats `requires:
 * code` as 'not runnable declaratively'.
 */
export type Requires = "code";

/**
 * Makes resume-token support declarative: the captured session_id is replayed into the
 * launch command on a retry/resume.
 */
export interface Resume {
    /**
     * Args appended when resuming, e.g. [--resume, "{resume.token}"].
     */
    arg?: string[];
    /**
     * The nudge statement sent on a park-resume relaunch (substituted for {task.statement}),
     * e.g. 'Continue — the requested permissions have been granted.'
     */
    prompt?: string;
}

/**
 * Opt-in isolation tier for the harness subprocess (executor-container-sandbox.md). Absent
 * ⇒ the native git-worktree sandbox (default; today's behaviour). `container` runs the
 * harness inside a docker|podman container with resource limits and enforced egress. A
 * global disable switch (SWARMKIT_DISABLE_CONTAINER_SANDBOX) forces the worktree regardless
 * of this block — the operator is never trapped by an archetype that insists on a container.
 */
export interface Sandbox {
    /**
     * Hosts the harness may reach when network=allowlist, e.g. [api.anthropic.com]. Empty ⇒
     * effectively deny (a strict base to add to).
     */
    allow?: string[];
    /**
     * Provision the harness into a derived image so it runs in-container with NO local install
     * — the user brings only their API key/subscription. Built once, content-addressed by the
     * resolved Dockerfile + context, and cached; alternative to a prebuilt `image`. Give
     * exactly one of `base` (+ optional `install`, the ergonomic path that keeps the adapter
     * self-contained), `dockerfile` (a path, for full Docker control), or `dockerfile_inline`
     * (Dockerfile content inline). `base`+`install` is lowered to a Dockerfile internally, so
     * all three go through one builder.
     */
    build?: Build;
    /**
     * A prebuilt container image for kind=container, in which the harness is already installed
     * — any image you choose (yours, or a public one). Falls back to $SWARMKIT_HARNESS_IMAGE
     * when unset. SwarmKit publishes no required image: if neither `image`, `build`, nor
     * $SWARMKIT_HARNESS_IMAGE is given, provisioning fails with a clear error rather than
     * guessing. Secrets are never baked in — they are injected at run via -e (same rule as the
     * MCP sandbox). Use `build` instead to provision the harness into a derived image with no
     * local install.
     */
    image?: string;
    /**
     * `worktree` (default) runs the harness directly in an ephemeral git worktree, as today.
     * `container` provisions an isolated container with the worktree bind-mounted read-write,
     * resource limits, and the `network` policy enforced.
     */
    kind?: SandboxKind;
    /**
     * Extra resources bind-mounted into the container beyond the worktree — a source tree, a
     * knowledge-base directory, shared read-only config. Secrets do NOT belong here (use auth
     * modes / -e injection).
     */
    mounts?: Mount[];
    /**
     * Enforced egress policy for kind=container. `deny` → no outbound access (--network none);
     * suits a local-model harness. `allowlist` → egress only to `allow` hosts, via a managed
     * forward proxy — the mode a cloud harness needs to reach its model API and nothing else.
     */
    network?: Network;
    /**
     * Container resource limits for kind=container. Omitted fields are unbounded.
     */
    resources?: Resources;
}

/**
 * Provision the harness into a derived image so it runs in-container with NO local install
 * — the user brings only their API key/subscription. Built once, content-addressed by the
 * resolved Dockerfile + context, and cached; alternative to a prebuilt `image`. Give
 * exactly one of `base` (+ optional `install`, the ergonomic path that keeps the adapter
 * self-contained), `dockerfile` (a path, for full Docker control), or `dockerfile_inline`
 * (Dockerfile content inline). `base`+`install` is lowered to a Dockerfile internally, so
 * all three go through one builder.
 */
export interface Build {
    /**
     * Base image the install steps run on, e.g. node:22-slim. Lowered to `FROM <base>` + a
     * `RUN` per install step.
     */
    base?: string;
    /**
     * Path to a Dockerfile (relative → workspace root) for harnesses needing COPY / multi-stage
     * / ARG beyond base+install. The build context is the file's directory.
     */
    dockerfile?: string;
    /**
     * Dockerfile content inline — full control while keeping the adapter a single
     * self-contained artifact (no second file to ship).
     */
    dockerfile_inline?: string;
    /**
     * Shell commands run at build time to install the harness into the image, e.g. ["npm
     * install -g @anthropic-ai/claude-code"]. Only used with `base`. No secrets here — install
     * must not need the user's credential (that arrives at run via -e).
     */
    install?: string[];
}

/**
 * `worktree` (default) runs the harness directly in an ephemeral git worktree, as today.
 * `container` provisions an isolated container with the worktree bind-mounted read-write,
 * resource limits, and the `network` policy enforced.
 */
export type SandboxKind = "worktree" | "container";

export interface Mount {
    /**
     * Read-only (default) or read-write.
     */
    mode?: Mode;
    /**
     * Host path; a relative path resolves under the workspace root.
     */
    source: string;
    /**
     * Absolute path the resource is mounted at inside the container.
     */
    target: string;
}

/**
 * Read-only (default) or read-write.
 */
export type Mode = "ro" | "rw";

/**
 * Enforced egress policy for kind=container. `deny` → no outbound access (--network none);
 * suits a local-model harness. `allowlist` → egress only to `allow` hosts, via a managed
 * forward proxy — the mode a cloud harness needs to reach its model API and nothing else.
 */
export type Network = "deny" | "allowlist";

/**
 * Container resource limits for kind=container. Omitted fields are unbounded.
 */
export interface Resources {
    /**
     * Max CPUs, passed to --cpus (e.g. "2").
     */
    cpus?: string;
    /**
     * Max memory, passed to --memory (e.g. "2g").
     */
    memory?: string;
    /**
     * Max process count, passed to --pids-limit.
     */
    pids?: number;
}

export interface Stream {
    /**
     * Line-delimited JSON only (RFC decision 1a).
     */
    format: Format;
    /**
     * Tee each untranslated vendor line as exec.raw for a forensic trail.
     */
    retain_raw?: boolean;
}

export type Format = "jsonl";

/**
 * Terminal success predicate. Core layers its semantic check (typed output +
 * artifact-manifest match) on top — exit code alone is necessary, not sufficient.
 */
export interface SuccessWhen {
    exit_code?: number;
}

/**
 * `opaque` (unobservable) adapters are denied by default (RFC decision 5); use requires
 * explicit per-archetype opt-in.
 */
export type TelemetryGrade = "normalized" | "opaque";

