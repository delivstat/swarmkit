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
    apiVersion: APIVersion;
    artifacts?: Artifacts;
    kind:       Kind;
    metadata:   Metadata;
    runtime?:   Runtime;
}

export interface Agents {
    root: Root;
}

export interface Root {
    archetype?: string;
    /**
     * Nested agents. Tree structure, one parent per agent (design §5.2).
     */
    children?: ChildAgent[];
    iam?:      Iam;
    id:        string;
    model?:    Model;
    prompt?:   Prompt;
    role:      RootRole;
    /**
     * Skill IDs (design §6.1). Replaces the archetype's skill list when present.
     */
    skills?: string[];
    /**
     * Skills merged onto the archetype defaults (design §6.6).
     */
    skills_additional?: string[];
}

export interface ChildAgent {
    archetype?: string;
    /**
     * Nested agents. Tree structure, one parent per agent (design §5.2).
     */
    children?: ChildAgent[];
    iam?:      Iam;
    id:        string;
    model?:    Model;
    prompt?:   Prompt;
    role:      ChildRole;
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

export interface Model {
    max_tokens?: number;
    name?:       string;
    /**
     * Typically anthropic | openai | google | azure | local | custom.
     */
    provider?:    string;
    temperature?: number;
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

