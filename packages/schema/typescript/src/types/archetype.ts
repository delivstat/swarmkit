/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * A kind of agent (noun). See design §6.6, §13 and design/details/archetype-schema-v1.md.
 */
export interface SwarmKitArchetype {
    apiVersion: APIVersion;
    defaults:   Defaults;
    kind:       Kind;
    metadata:   Metadata;
    provenance: Provenance;
    /**
     * Must match the agent role where this archetype is instantiated.
     */
    role: Role;
}

export type APIVersion = "swarmkit/v1";

export interface Defaults {
    iam?:           Iam;
    model?:         Model;
    output_schema?: { [key: string]: any } | null;
    prompt?:        Prompt;
    skills?:        SkillElement[];
}

export interface Iam {
    base_scope?:      string[];
    elevated_scopes?: string[];
}

export interface Model {
    max_tokens?: number;
    name?:       string;
    /**
     * Provider-native runtime options passed through to the model call (e.g. Ollama num_ctx /
     * repeat_penalty, OpenAI top_p / frequency_penalty, Google top_k). Applied after the
     * first-class fields, so an option with the same name (e.g. temperature) overrides them.
     * Keys must be valid for the resolved provider.
     */
    options?:     { [key: string]: any };
    provider?:    string;
    temperature?: number;
    /**
     * Model name for tool-calling turns. When set, tool loop uses this cheaper model and
     * synthesis uses the main model.
     */
    tool_model?: string;
    /**
     * Model provider for tool-calling turns (cheaper model). Falls back to provider if unset.
     */
    tool_provider?: string;
    [property: string]: any;
}

export interface Prompt {
    persona?: string;
    system?:  string;
    [property: string]: any;
}

export type SkillElement = SkillClass | string;

export interface SkillClass {
    abstract: Abstract;
}

export interface Abstract {
    /**
     * Free-text tag matched by the topology resolver.
     */
    capability?: string;
    category:    Category;
}

export type Category = "capability" | "decision" | "coordination" | "persistence";

export type Kind = "Archetype";

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

/**
 * Must match the agent role where this archetype is instantiated.
 */
export type Role = "root" | "leader" | "worker";

