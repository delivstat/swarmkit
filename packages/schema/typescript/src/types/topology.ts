/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * A complete swarm definition. See design §10 and design/details/topology-schema-v1.md.
 */
export interface SwarmKitTopology {
    agents: Agents;
    /**
     * Schema version. Breaking changes bump this major.
     */
    apiVersion:         APIVersion;
    artifacts?:         Artifacts;
    governance?:        Governance;
    intent_monitoring?: IntentMonitoring;
    kind:               Kind;
    metadata:           Metadata;
    runtime?:           Runtime;
}

export interface Agents {
    root: Root;
}

export interface Root {
    archetype?: string;
    /**
     * Nested agents. Tree structure, one parent per agent (design §5.2).
     */
    children?: ChildElement[];
    /**
     * Optional per-artifact quality gate on this agent's output: validate -> judge -> (review)
     * -> multi-party human approval (design/details/gate-funnel.md). Validated against the
     * funnel schema at load time. When present, the agent's output must clear the automated
     * layers and a human approval before the run advances.
     */
    funnel?:            { [key: string]: any };
    iam?:               Iam;
    id:                 string;
    intent_monitoring?: IntentMonitoring;
    model?:             Model;
    output_schema?:     { [key: string]: any } | null;
    prompt?:            Prompt;
    role:               RootRole;
    /**
     * Skill IDs (design §6.1). Replaces the archetype's skill list when present.
     */
    skills?: string[];
    /**
     * Skills merged onto the archetype defaults (design §6.6).
     */
    skills_additional?: string[];
}

export interface ChildElement {
    archetype?: string;
    /**
     * Nested agents. Tree structure, one parent per agent (design §5.2).
     */
    children?: ChildElement[];
    /**
     * Optional per-artifact quality gate on this agent's output: validate -> judge -> (review)
     * -> multi-party human approval (design/details/gate-funnel.md). Validated against the
     * funnel schema at load time. When present, the agent's output must clear the automated
     * layers and a human approval before the run advances.
     */
    funnel?:            { [key: string]: any };
    iam?:               Iam;
    id:                 string;
    intent_monitoring?: IntentMonitoring;
    model?:             Model;
    output_schema?:     { [key: string]: any } | null;
    prompt?:            Prompt;
    role:               ChildRole;
    /**
     * Skill IDs (design §6.1). Replaces the archetype's skill list when present.
     */
    skills?: string[];
    /**
     * Skills merged onto the archetype defaults (design §6.6).
     */
    skills_additional?: string[];
}

export interface Iam {
    base_scope?:      string[];
    elevated_scopes?: string[];
}

/**
 * Optional intent drift detection. Monitors semantic drift from the original goal. See
 * design/details/intent-drift-detection.md.
 */
export interface IntentMonitoring {
    /**
     * Enable drift detection for this topology.
     */
    enabled?: boolean;
    /**
     * Strategy when drift exceeds threshold. log=audit only, warn=log+event, nudge=inject
     * refocus message.
     */
    on_drift?: OnDrift;
    /**
     * Drift score above which action is taken. Default: 0.75. Range guide: 0.5=aggressive,
     * 0.75=balanced, 0.9=permissive.
     */
    threshold?: number;
}

/**
 * Strategy when drift exceeds threshold. log=audit only, warn=log+event, nudge=inject
 * refocus message.
 */
export type OnDrift = "log" | "warn" | "nudge";

export interface Model {
    max_tokens?: number;
    name?:       string;
    /**
     * Provider-native runtime options passed through to the model call (e.g. Ollama num_ctx /
     * repeat_penalty, OpenAI top_p / frequency_penalty, Google top_k). Applied after the
     * first-class fields, so an option with the same name (e.g. temperature) overrides them.
     * Keys must be valid for the resolved provider.
     */
    options?: { [key: string]: any };
    /**
     * Typically anthropic | openai | google | azure | local | custom.
     */
    provider?:    string;
    temperature?: number;
    /**
     * Model name for tool-calling turns. Uses a cheaper model for tool calls, main model for
     * synthesis.
     */
    tool_model?: string;
    /**
     * Model provider for tool-calling turns. Falls back to provider if unset.
     */
    tool_provider?: string;
    [property: string]: any;
}

export interface Prompt {
    persona?: string;
    system?:  string;
    [property: string]: any;
}

export type ChildRole = "leader" | "worker";

export type RootRole = "root";

export type APIVersion = "swarmkit/v1";

export interface Artifacts {
    audit?:           Audit;
    knowledge_bases?: { [key: string]: any }[];
    review_queues?:   { [key: string]: any }[];
    /**
     * See design §12.1.
     */
    skill_gap_logging?: SkillGapLogging;
}

export interface Audit {
    level?:          Level;
    retention_days?: number;
    storage?:        Storage;
}

export type Level = "minimal" | "standard" | "detailed";

export type Storage = "sqlite" | "postgres";

/**
 * See design §12.1.
 */
export interface SkillGapLogging {
    enabled?:           boolean;
    surface_threshold?: number;
}

/**
 * Topology-level governance overrides. Inherits from workspace governance; entries here
 * override or extend by id.
 */
export interface Governance {
    /**
     * Decision skill bindings that override or extend workspace-level bindings. Same id =
     * override, new id = extend, required: false = disable inherited.
     */
    decision_skills?: DecisionSkillElement[];
}

/**
 * Binds a decision skill to a trigger point.
 */
export interface DecisionSkillElement {
    /**
     * Skill-specific configuration.
     */
    config?: { [key: string]: any };
    /**
     * Decision skill ID.
     */
    id: string;
    /**
     * Set false to disable an inherited workspace binding.
     */
    required?: boolean;
    /**
     * Comma-separated agent IDs. Default '*' = all agents.
     */
    scope?: string;
    /**
     * When the skill fires: pre_input (before agent runs), post_output (after agent output),
     * checkpoint (between task batches), pre_synthesis (before final synthesis).
     */
    trigger: Trigger;
}

/**
 * When the skill fires: pre_input (before agent runs), post_output (after agent output),
 * checkpoint (between task batches), pre_synthesis (before final synthesis).
 */
export type Trigger = "pre_input" | "post_output" | "checkpoint" | "pre_synthesis";

export type Kind = "Topology";

export interface Metadata {
    description?: string;
    name:         string;
    version:      string;
}

export interface Runtime {
    checkpointing?:        Checkpointing;
    max_concurrent_tasks?: number;
    /**
     * Execution mode (design §14.1).
     */
    mode?:                 Mode;
    planning?:             Planning;
    synthesis?:            Synthesis;
    task_timeout_seconds?: number;
}

export interface Checkpointing {
    storage?: Storage;
    [property: string]: any;
}

/**
 * Execution mode (design §14.1).
 */
export type Mode = "one-shot" | "persistent" | "scheduled";

/**
 * Controls task planning and scope behavior for leader agents. When set, the compiler
 * enforces structured planning instead of relying on prompt instructions.
 */
export interface Planning {
    /**
     * Leaders must call create-scope before synthesis. Blocks synthesis if no scope exists.
     */
    scope_required?: boolean;
    /**
     * Agent roles treated as synthesis/output roles by the planner: they are auto-wired to
     * depend on research tasks so they run last, not in parallel. Defaults to ['self',
     * 'document-writer']. 'self' is always a structural synthesis role even if omitted.
     */
    synthesis_roles?: string[];
    /**
     * Role name for the automatic synthesis step invoked when synthesis config is set. Defaults
     * to 'synthesizer'.
     */
    synthesizer_role?: string;
    /**
     * Enforce two-phase planning: Phase 1 (research) → create-scope → Phase 2 (targeted tasks).
     * The compiler auto-injects checkpoint prompts.
     */
    two_phase?: boolean;
}

/**
 * Topology-level synthesis config. Overrides workspace-level synthesis. When set, the
 * compiler invokes a large-context model with all research results instead of having the
 * architect write the document.
 */
export interface Synthesis {
    /**
     * Model name. Overrides workspace synthesis model.
     */
    model?: string;
    /**
     * Custom system prompt for the synthesizer. Overrides workspace-level prompt.
     */
    prompt?: string;
    /**
     * Model provider ID. Overrides workspace synthesis provider.
     */
    provider?: string;
}

