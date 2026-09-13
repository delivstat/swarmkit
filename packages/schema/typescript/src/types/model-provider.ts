/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * A declarative model provider (design/details/declarative-model-providers.md). A provider
 * is DATA: a base URL, an auth shape, a model catalogue, capabilities and request quirks,
 * layered over a wire-format FAMILY that stays code (openai-compatible, anthropic, google,
 * ollama). `extends` names a family or another provider; fields merge one hop at a time.
 * Capabilities may only NARROW what the family offers. Anything past the DSL's ceiling
 * declares `requires: code` and is refused at load rather than half-working.
 */
export interface SwarmKitModelProvider {
    apiVersion: APIVersion;
    kind:       Kind;
    metadata:   Metadata;
    provenance: Provenance;
    spec:       Spec;
}

export type APIVersion = "swarmkit/v1";

export type Kind = "ModelProvider";

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
    auth?: Auth;
    /**
     * Endpoint. ${VAR:-default} substitution applies.
     */
    base_url?:     string;
    capabilities?: Capabilities;
    /**
     * A wire-format family (openai-compatible, anthropic, google, ollama) or another provider's
     * id. Resolved at load; the chain must end at a family and may not revisit an id.
     */
    extends: string;
    /**
     * Fields merged into the request body that the family's base API does not define, e.g.
     * OpenRouter's usage.include. openai-compatible only.
     */
    extra_body?: { [key: string]: any };
    /**
     * Static extra request headers, e.g. OpenRouter's HTTP-Referer.
     */
    headers?: { [key: string]: string };
    models?:  Models;
    options?: Options;
    /**
     * Declares this provider cannot be expressed declaratively. Load refuses it with the reason
     * — the ceiling fails loudly rather than half-working.
     */
    requires?: Requires;
}

/**
 * One shape: a static key in a header. Absent means no auth — a local runtime. OAuth and
 * service accounts are the requires-code case.
 */
export interface Auth {
    /**
     * Environment variable holding the key. The provider registers only when it is set.
     */
    api_key_env?: string;
    header?:      string;
    /**
     * Prefix before the key; empty string for a bare key (Azure's api-key header).
     */
    scheme?: string;
}

/**
 * May only NARROW the family. A true the family does not offer is a load-time error.
 */
export interface Capabilities {
    images?:            boolean;
    streaming?:         boolean;
    structured_output?: boolean;
    tools?:             boolean;
}

export interface Models {
    /**
     * Aggregators and local servers: the catalogue is unbounded and the server validates.
     */
    accept_any?: boolean;
    /**
     * What supports() accepts. Default comes from the family.
     */
    pattern?: string;
}

export interface Options {
    /**
     * Option keys moved from `options` to the payload root — Ollama's think and keep_alive.
     * ollama family only.
     */
    lift_to_root?: string[];
}

/**
 * Declares this provider cannot be expressed declaratively. Load refuses it with the reason
 * — the ceiling fails loudly rather than half-working.
 */
export type Requires = "code";

