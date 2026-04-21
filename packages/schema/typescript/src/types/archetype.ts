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
    iam?:    Iam;
    model?:  Model;
    prompt?: Prompt;
    skills?: SkillElement[];
}

export interface Iam {
    base_scope?:      string[];
    elevated_scopes?: string[];
}

export interface Model {
    max_tokens?:  number;
    name?:        string;
    provider?:    string;
    temperature?: number;
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

