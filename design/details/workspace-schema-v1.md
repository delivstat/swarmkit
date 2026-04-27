---
title: Workspace schema v1
description: Deployment-level config — governance provider, identity, model providers, MCP registry, credentials, storage.
tags: [schema, workspace, m0]
status: implemented
---

# Workspace schema v1

**Scope:** `packages/schema`
**Design reference:** §5.1 (four-level hierarchy), §9.3 (workspace structure), §8.5 (GovernanceProvider), §16 (Identity / Access / Governance), §18 (MCP integration), plus `design/details/model-provider-abstraction.md`.
**Status:** in review

## Goal

Define the `workspace.yaml` top-level artifact — the deployment-level configuration file that ties everything under a Swael workspace together: governance provider, identity provider, model provider registrations, MCP server registry, storage backends, and workspace identity. Every topology, every skill, every archetype in a workspace inherits from this file.

## Non-goals

- **Credentials storage.** Credentials live in env vars, sealed secrets, or external vaults; workspace.yaml carries *references* to those, never literal values.
- **Per-environment overlays** (dev / staging / prod) — deferred to a later minor. Workspaces are deployment-scoped; run the same workspace against different env var sets to vary environments.
- **Organisation / Team management** — §5.1 makes these levels 1 and 2 of the hierarchy. v1 treats them as implicit (single org, single team per install); schema accepts optional fields for forward compatibility.
- **MCP server lifecycle / sandboxing rules** — the registry lives here; the sandboxing enforcement is runtime (M5).

## API shape

### Top-level structure

```yaml
apiVersion: swael/v1
kind: Workspace
metadata:
  id: acme-engineering
  name: Acme Engineering Swarms
  description: |
    Production workspace running the Code Review Swarm and the Skill
    Authoring Swarm for the Acme engineering org.

# Level 1-2 (design §5.1) — optional in v1, accepted for forward compat.
organisation:
  id: acme
  name: Acme Inc.
team:
  id: engineering
  name: Engineering

# Which GovernanceProvider (design §8.5) and its config.
governance:
  provider: agt                     # agt | mock | custom
  policy_language: yaml             # yaml | rego | cedar  (design §21)
  config:
    policies_dir: ./policies
    trust_decay_half_life_hours: 168

# Human-identity provider (design §16.1).
identity:
  provider: builtin                 # builtin | auth0 | okta | google | azure-ad | oidc
  config:
    session_ttl_hours: 24

# Custom ModelProvider registration (design/details/model-provider-abstraction.md).
# Built-ins (anthropic, openai, google, ollama, mock) auto-register; this section
# is for org-internal or unpublished-to-PyPI providers.
model_providers:
  - class: acme_internal_llm.InternalModelProvider
    provider_id: acme-internal
    config:
      base_url: https://llm.acme.internal
      # Credentials by env-var reference; never literal.
      auth_token_env: ACME_LLM_TOKEN

# Credential references used across the workspace. Never literal values.
credentials:
  anthropic_api_key:
    source: env                     # env | file | vault
    env: ANTHROPIC_API_KEY
  google_api_key:
    source: vault
    vault: { path: secret/data/swael/google, field: api_key }

# MCP server registry (design §18). Skills reference servers by ID.
mcp_servers:
  - id: github
    transport: stdio                # stdio | http
    command: ["mcp-github"]         # when transport=stdio
    credentials_ref: github_pat
    sandboxed: false                # trusted external server
  - id: rynko_flow
    transport: http
    endpoint: https://mcp.rynko.dev
    credentials_ref: rynko_api_key
    sandboxed: false
  - id: acme_internal
    transport: stdio
    command: ["python", "-m", "acme_swael.internal_mcp"]
    sandboxed: true                 # scaffolded / untrusted — Docker-run per §8.8

# Storage backends for checkpoints, audit log, knowledge bases.
storage:
  checkpoints:
    backend: sqlite                 # sqlite | postgres
    path: ./.swael/state
  audit:
    backend: agt                    # agt | sqlite | postgres
    retention_days: 365
  knowledge_bases:
    default_backend: sqlite
```

### `governance` block

```yaml
governance:
  provider: agt | mock | custom
  policy_language: yaml | rego | cedar    # §21 open question — default yaml
  config:                                  # passthrough to the chosen provider
    ...
```

- `provider: agt` is the v1.0 default (design §8.5 / §16). `provider: mock` is test-only. `provider: custom` requires a corresponding entry in `model_providers`-style extension — a future GovernanceProvider plugin path (M2). Schema already accepts the string so plugin landings don't need a schema bump.
- `policy_language` reflects §21's open question. v1.0 ships `yaml`; `rego` and `cedar` are supported-but-advanced.

### `identity` block

```yaml
identity:
  provider: builtin | auth0 | okta | google | azure-ad | oidc
  config:
    ...
```

Design §16.1 explicitly lists these providers. `builtin` is the v1.0 default for solo developers and small teams; others land as v1.x adapters. Schema accepts all six names from day one so adapter PRs don't need schema bumps.

### `model_providers` block

```yaml
model_providers:
  - class: <fully-qualified Python class path>
    provider_id: <unique slug>
    config: { ... }
```

Per `design/details/model-provider-abstraction.md`, custom providers discovered via Python entry points are registered automatically; this block is for org-internal / unpublished providers registered by class path. Empty / omitted means "use only built-ins and entry-point-discovered providers."

### `credentials` block

Every credential declaration shares a **uniform shape**: `{ source, config }`. The `source` selects the SecretsProvider; `config` carries provider-specific properties. Mirrors the `GovernanceProvider` and `ModelProvider` patterns — narrow abstraction, several built-in implementations, plugin path for custom.

```yaml
credentials:
  anthropic_api_key:
    source: env
    config: { env: ANTHROPIC_API_KEY }

  openai_api_key:
    source: hashicorp-vault
    config:
      address: https://vault.acme.internal
      path: secret/data/swael/openai
      field: api_key

  github_pat:
    source: aws-secrets-manager
    config: { secret_id: swael/github, region: us-east-1 }

  google_api_key:
    source: gcp-secret-manager
    config: { project: acme-123, secret: swael-google-key, version: latest }

  azure_api_key:
    source: azure-key-vault
    config: { vault_url: https://acme.vault.azure.net, secret_name: swael-azure-key }

  bootstrap_cert:
    source: file
    config: { path: /etc/swael/bootstrap.pem }

  internal_token:
    source: plugin
    provider_id: acme-internal-secrets   # names a SecretsProvider registered via entry points
    config: { service: swael, key: internal-token }
```

**v1.0 built-in sources** (`source` enum):

| source | Backing | Config shape (runtime-validated) |
|---|---|---|
| `env` | OS environment | `{ env: <VAR_NAME> }` |
| `file` | Filesystem | `{ path: <absolute or workspace-relative> }` |
| `hashicorp-vault` | HashiCorp Vault | `{ address, path, field }` |
| `aws-secrets-manager` | AWS Secrets Manager | `{ secret_id, region }` |
| `gcp-secret-manager` | GCP Secret Manager | `{ project, secret, version }` |
| `azure-key-vault` | Azure Key Vault | `{ vault_url, secret_name }` |
| `plugin` | Entry-point-discovered or workspace-registered `SecretsProvider` | arbitrary; `provider_id` required |

**Never stores literal credentials.** The workspace.yaml is a shareable artifact (checked into git); real secrets live elsewhere. Every entry is a reference; the runtime resolves it at load time using the configured source. The schema enforces `additionalProperties: false` at the top of each credential entry so a `value:` field can't sneak past.

**Runtime validation of `config`.** The schema does not validate per-source config shape — that would require one `oneOf` branch per source and make the schema brittle. Instead each SecretsProvider validates its own `config` at load time; the table above is the informal contract.

**SecretsProvider abstraction (follow-up).** The runtime implementation lives behind a `SecretsProvider` ABC paralleling `ModelProvider` (see `design/details/model-provider-abstraction.md`). Built-in providers auto-register; third-party providers discover via Python entry points (`swael.secrets_providers`). A full `design/details/secrets-provider-abstraction.md` lands alongside the M2 governance work — not blocking this PR, but tracked.

### `mcp_servers` block

MCP servers are registered here (design §18). Skills reference servers by ID (`implementation.server: github`), so exactly one registry is needed per workspace. Each entry carries enough metadata for the runtime to launch (stdio) or connect (http) to the server, plus the sandboxing flag that gates Docker-run supervision (§8.8).

- `transport: stdio` — server is a local process the runtime spawns; requires `command` (array).
- `transport: http` — server is reachable via HTTP; requires `endpoint`.
- `credentials_ref` names an entry in the `credentials` block.
- `sandboxed: true` forces Docker-or-equivalent isolation (design §8.8) — required for scaffolded servers authored by swarms; optional for trusted external servers.

### `storage` block

Checkpointing, audit, and knowledge-base backends. SQLite is the v1.0 default for all three — zero-config, good up to mid-hundreds of concurrent tasks. Postgres is a drop-in replacement for production workloads; lands as config only, no schema bump needed.

Audit `backend: agt` defers to AGT's Agent SRE append-only storage (design §16.4). `sqlite` / `postgres` are fallbacks for non-AGT deployments; the runtime enforces append-only semantics at the SQLAlchemy layer.

### Naming convention — MCP server IDs

MCP server `id` uses the repo-wide lowercase-kebab identifier pattern (`^[a-z][a-z0-9-]*$`). Skills reference servers by this ID through `implementation.server`, which is currently an unconstrained string (runtime-validated against the workspace registry at M1). Some existing skill fixtures reference snake_case server names (e.g. `rynko_flow` in the merged §6.3 adaptation); the M1 resolver PR will clean up that cross-artifact inconsistency when it lands.

## What's not in the schema

- **Topology list** — the runtime discovers topologies from the `topologies/` directory (design §9.3). No need to enumerate them in the workspace file.
- **Scope vocabulary** — workspace.yaml does not redefine IAM scopes. Scopes are declared by the skills (design §6.3) and enforced by the GovernanceProvider.
- **Pre-flight checks** — whether all MCP servers are reachable, whether credentials exist, etc. — are runtime concerns, not schema concerns.
- **Schedules and triggers** — separate artifacts under `schedules/` / `triggers/` with their own schema (Task #13).

## Test plan

Following `docs/notes/schema-change-discipline.md`:

- **Valid fixtures** under `packages/schema/tests/fixtures/workspace/`:
  - `minimal.yaml` — smallest valid workspace (metadata only; defaults apply).
  - `full.yaml` — every block populated; proves the full surface validates.
  - `with-model-providers.yaml` — exercises custom provider registration.
  - `with-mcp-servers.yaml` — mixes stdio + http + sandboxed servers.
  - `vault-credentials.yaml` — exercises every v1.0 secrets source (hashicorp-vault, aws-secrets-manager, gcp-secret-manager, azure-key-vault, plugin, file).
- **Invalid fixtures** under `packages/schema/tests/fixtures/workspace-invalid/`:
  - `missing-metadata.yaml`
  - `bad-governance-provider.yaml` — value not in the enum
  - `bad-policy-language.yaml`
  - `bad-identity-provider.yaml`
  - `mcp-stdio-missing-command.yaml` — stdio transport must have `command`
  - `mcp-http-missing-endpoint.yaml` — http transport must have `endpoint`
  - `model-provider-missing-class.yaml`
  - `credential-literal-in-workspace.yaml` — literal-value field rejected by uniform `additionalProperties: false` shape
  - `credential-plugin-missing-provider-id.yaml` — `source: plugin` requires `provider_id`
  - `credential-bad-source.yaml` — source outside the v1.0 enum; use `source: plugin` for unlisted providers
- **Python test:** extends `test_schemas.py` with parametrised workspace cases.
- **TS test:** single `describeFixtures` line.

## Demo plan

`just demo-workspace-schema` via the existing parametrised runner.

## Open questions

- **SecretsProvider abstraction design note.** Schema commits to the `source` enum and uniform `{source, config}` shape; the ABC, registry, and per-source adapter contracts are documented in a follow-up `design/details/secrets-provider-abstraction.md`. Parallel of `model-provider-abstraction.md`. Lands with M2 governance work.
- **1Password / Bitwarden / Infisical etc.** — reachable today via `source: plugin` with `provider_id`; may be promoted to built-ins in a later minor based on community demand.
- **Policy language default.** Tracked as §21 open question. Recommending `yaml` for v1.0 — simplest for users. Schema accepts all three so the decision doesn't gate this PR.
- **MCP server credentials scoping.** An MCP server credentials_ref currently points at a workspace-level credential. Should per-server credential scoping exist? Sufficient for v1.0; revisit if multi-tenant workspaces emerge.
- **`organisation` / `team` at v2.0 multi-user.** Schema accepts them now as optional objects with `id` + `name`. Richer membership structure lands when multi-user (v2.0) arrives.

## Follow-ups (separate PRs)

- `design/details/secrets-provider-abstraction.md` — mirror of model-provider-abstraction.md.
- Runtime adapter implementations per v1.0 source.
- Trigger schema v1 (Task #13) — schedules, webhooks, file_watch live alongside workspace.
- Pydantic + TS codegen (Tasks #14, #15).
- Aggregate demo (Task #16) — `just demo-schema` will include workspace after this lands.
- Runtime workspace loader (M1) — consumes this schema.
