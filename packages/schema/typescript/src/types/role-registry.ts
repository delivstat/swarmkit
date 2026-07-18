/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * Workspace-level IAM registry mapping roles to the human identities that hold them and the
 * governance scopes they confer. A role carries many scopes (RBAC: identity -> role ->
 * scopes), so membership lives in one place per person. Consumed by multi-party approval
 * gates. See design/details/multi-party-approval.md.
 */
export interface SwarmKitRoleRegistry {
    apiVersion: APIVersion;
    kind:       Kind;
    metadata:   Metadata;
    /**
     * The roles in this workspace. Role ids must be unique (enforced at load time — JSON Schema
     * cannot express object-field uniqueness).
     */
    roles: SwarmKitRoleRegistr[];
}

export type APIVersion = "swarmkit/v1";

export type Kind = "RoleRegistry";

export interface Metadata {
    description?: string;
    id:           string;
    name:         string;
}

export interface SwarmKitRoleRegistr {
    id: string;
    /**
     * Human identity references that hold this role. May be empty for an unstaffed role; a gate
     * requiring an unstaffed role cannot reach quorum.
     */
    members: string[];
    /**
     * Governance scopes this role confers. A role carries many scopes so a handover is a single
     * membership edit.
     */
    scopes: string[];
}

