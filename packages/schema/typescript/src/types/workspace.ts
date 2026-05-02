/* eslint-disable */
/* biome-ignore-all */
// This file is generated from the canonical JSON Schema. Do not edit by hand.
// Regenerate with: just schema-codegen-ts
/**
 * Deployment-level config: identity, governance, model providers, MCP registry, storage.
 * See design §9.3 and design/details/workspace-schema-v1.md.
 */
export interface SwarmKitWorkspace {
    apiVersion:       APIVersion;
    credentials?:     { [key: string]: CredentialValue };
    governance?:      Governance;
    identity?:        Identity;
    kind:             Kind;
    mcp_servers?:     MCPServerElement[];
    metadata:         Metadata;
    model_providers?: ModelProviderElement[];
    organisation?:    Organisation;
    storage?:         Storage;
    team?:            Organisation;
}

export type APIVersion = "swarmkit/v1";

export interface CredentialValue {
    /**
     * Provider-specific configuration. Runtime validates shape per source.
     */
    config: { [key: string]: any };
    /**
     * Required when source=plugin. Names the registered SecretsProvider.
     */
    provider_id?: string;
    source:       Source;
}

export type Source = "env" | "file" | "hashicorp-vault" | "aws-secrets-manager" | "gcp-secret-manager" | "azure-key-vault" | "plugin";

export interface Governance {
    config?: { [key: string]: any };
    /**
     * §21 open question — default yaml for v1.0.
     */
    policy_language?: PolicyLanguage;
    /**
     * GovernanceProvider implementation (design §8.5).
     */
    provider: GovernanceProvider;
}

/**
 * §21 open question — default yaml for v1.0.
 */
export type PolicyLanguage = "yaml" | "rego" | "cedar";

/**
 * GovernanceProvider implementation (design §8.5).
 */
export type GovernanceProvider = "agt" | "mock" | "custom";

export interface Identity {
    config?: { [key: string]: any };
    /**
     * Human-identity provider (design §16.1).
     */
    provider: IdentityProvider;
}

/**
 * Human-identity provider (design §16.1).
 */
export type IdentityProvider = "builtin" | "auth0" | "okta" | "google" | "azure-ad" | "oidc";

export type Kind = "Workspace";

export interface MCPServerElement {
    /**
     * Required when transport=stdio.
     */
    command?:         string[];
    credentials_ref?: string;
    /**
     * Working directory for stdio servers. Supports ${VAR} expansion. Defaults to workspace
     * root.
     */
    cwd?: string;
    /**
     * Required when transport=http.
     */
    endpoint?: string;
    /**
     * Environment variables passed to a stdio server. Values support ${VAR} expansion from the
     * runtime process environment. Use `credentials_ref` for secrets; `env` is for
     * configuration.
     */
    env?: { [key: string]: string };
    id:   string;
    /**
     * Docker image for sandboxed servers. Defaults to swarmkit-mcp-sandbox (Python + mcp SDK).
     * Use node:22-slim for Node.js servers.
     */
    sandbox_image?: string;
    /**
     * True forces Docker-or-equivalent isolation (design §8.8).
     */
    sandboxed?: boolean;
    transport:  Transport;
}

export type Transport = "stdio" | "http";

export interface Metadata {
    /**
     * Arbitrary key-value metadata the runtime ignores. For enterprise use: cost_center, team,
     * environment, compliance tags, etc.
     */
    annotations?: { [key: string]: string };
    description?: string;
    id:           string;
    name:         string;
}

export interface ModelProviderElement {
    /**
     * Fully-qualified Python class path.
     */
    class:       string;
    config?:     { [key: string]: any };
    provider_id: string;
}

export interface Organisation {
    id:    string;
    name?: string;
}

export interface Storage {
    audit?:           Audit;
    checkpoints?:     Checkpoints;
    knowledge_bases?: KnowledgeBases;
}

export interface Audit {
    backend?:        AuditBackend;
    retention_days?: number;
    url?:            string;
}

export type AuditBackend = "agt" | "sqlite" | "postgres";

export interface Checkpoints {
    backend?: DefaultBackendEnum;
    path?:    string;
    url?:     string;
}

export type DefaultBackendEnum = "sqlite" | "postgres";

export interface KnowledgeBases {
    default_backend?: DefaultBackendEnum;
}

