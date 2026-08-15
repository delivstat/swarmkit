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
     * What this trigger fires, in parallel. Each item is either a topology id (fires that
     * topology) or an event target (delivers a correlated EventSignal to whatever the host
     * application registered).
     */
    targets: Target[];
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

export type Target = SwarmKitTrigge | string;

export interface SwarmKitTrigge {
    /**
     * How to derive the opaque correlation id from the incoming payload (e.g. a JSONPath like
     * $.body.correlation_id). Domain-neutral — the runtime models no business instance.
     */
    correlation_id?: string;
    /**
     * The event name to deliver, e.g. build.ready-in-qa. What it means is the receiving
     * application's decision — the runtime routes it and does not interpret it.
     */
    emit: string;
    /**
     * The event stream this signal belongs to — an opaque name the host application listens on.
     * Named `pipeline` for compatibility with triggers authored before SwarmKit stopped
     * sequencing (runtime 1.189.0); the runtime resolves it against no artifact.
     */
    pipeline: string;
}

/**
 * Discriminator; per-type config shape validated at runtime.
 */
export type Type = "cron" | "webhook" | "file_watch" | "manual" | "plugin";

