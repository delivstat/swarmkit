/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * An integration contract: the agreed interface between two (or more) applications,
 * identified by id. A first-class artifact so a pipeline's stage `locks` reference real
 * contracts (a checked, pickable vocabulary) instead of free strings — no typo'd lock
 * silently fails to serialise two requirements. The contract is not executed; it makes lock
 * ids real and carries which apps it binds. See design/details/contract-registry.md.
 */
export interface SwarmKitContract {
    apiVersion: APIVersion;
    /**
     * Optional pointer to where the interface itself lives (an API/event schema). Not
     * interpreted by core — documentation + a handle for reviewers.
     */
    interface?: string;
    kind:       Kind;
    metadata:   Metadata;
    /**
     * The applications this contract binds (at least two) — what makes it a contract (an
     * interface between apps). Drives the pipeline's contention/ownership display. App ids are
     * free strings.
     */
    parties:    string[];
    provenance: Provenance;
}

export type APIVersion = "swarmkit/v1";

export type Kind = "Contract";

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

