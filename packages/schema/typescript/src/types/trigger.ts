/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * Workspace-level schedule / webhook / file-watch / manual / plugin trigger. Unifies
 * schedules and triggers under one kind. See design §5.4 and
 * design/details/trigger-schema-v1.md.
 */
export interface SwarmKitTrigger {
    apiVersion: APIVersion;
    /**
     * Type-specific configuration. Runtime validates shape per type.
     */
    config?: Config;
    /**
     * Default true. Disabled triggers load but do not fire.
     */
    enabled?: boolean;
    kind:     Kind;
    metadata: Metadata;
    /**
     * Required when type=plugin. Names a registered TriggerProvider.
     */
    provider_id?: string;
    /**
     * Topology IDs this trigger fires. Fired in parallel.
     */
    targets: string[];
    /**
     * Discriminator; per-type config shape validated at runtime.
     */
    type: Type;
}

export type APIVersion = "swarmkit/v1";

/**
 * Type-specific configuration. Runtime validates shape per type.
 */
export interface Config {
    /**
     * Only meaningful for type=webhook.
     */
    auth?: Auth;
    [property: string]: any;
}

/**
 * Only meaningful for type=webhook.
 */
export interface Auth {
    /**
     * Name of a workspace `credentials` entry holding the secret.
     */
    credentials_ref: string;
    /**
     * HTTP header carrying the auth material (method-dependent default).
     */
    header?: string;
    method:  Method;
}

export type Method = "hmac" | "bearer" | "api_key";

export type Kind = "Trigger";

export interface Metadata {
    description?: string;
    id:           string;
    name:         string;
}

/**
 * Discriminator; per-type config shape validated at runtime.
 */
export type Type = "cron" | "webhook" | "file_watch" | "manual" | "plugin";

