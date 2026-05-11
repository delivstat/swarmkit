---
title: Workspace environment configuration
description: Separate environment-specific values (URLs, credentials, feature flags) from structural workspace config. Single interpolation point.
tags: [runtime, schema, configuration]
status: draft
---

# Workspace environment configuration

**Scope:** runtime, schema (workspace extension)
**Design reference:** §9.3 (workspace config), `product-architecture.md`
**Status:** draft — implement between M6 and M7

## Goal

Separate environment-specific values from structural workspace configuration so the same workspace can run in dev, staging, and prod without editing `workspace.yaml`.

## Problem

Today, `workspace.yaml` mixes structural config (what agents exist, which MCP servers to use) with environment config (webhook URLs, API keys, provider endpoints). This means:

- Can't commit `workspace.yaml` to git without leaking credentials
- Swapping environments requires editing the workspace file
- `${ENV_VAR}` interpolation happens everywhere, making it hard to audit what's secret and what's not
- Team members need different configs for the same workspace

## Design

### File layout

```
workspace/
├── workspace.yaml              # structural — committed to git
├── workspace.env.yaml          # environment — in .gitignore
├── workspace.env.prod.yaml     # optional per-environment overrides
├── topologies/
├── skills/
└── archetypes/
```

### workspace.yaml — references properties

```yaml
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: my-swarm
  name: My Swarm

mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: ${github.token}

notifications:
  - provider: slack
    config:
      webhook_url: ${notifications.slack.webhook_url}
      channel: ${notifications.slack.channel}

governance:
  provider: agt
  config:
    policies_dir: ${governance.policies_dir}

telemetry:
  exporter: ${telemetry.exporter}
  endpoint: ${telemetry.endpoint}
```

### workspace.env.yaml — actual values

```yaml
# NOT committed to git — add to .gitignore
# This is the ONLY file that does ${ENV_VAR} interpolation

github:
  token: ${GITHUB_TOKEN}

notifications:
  slack:
    webhook_url: ${SLACK_WEBHOOK_URL}
    channel: "#swarmkit-dev"

governance:
  policies_dir: ./policies

telemetry:
  exporter: console
  endpoint: http://localhost:4318/v1/traces

model:
  provider: openrouter
  name: meta-llama/llama-3.3-70b-instruct
```

### Resolution order

1. Environment-specific file (`workspace.env.{SWARMKIT_ENV}.yaml`) if `SWARMKIT_ENV` is set
2. Default env file (`workspace.env.yaml`)
3. Environment variables for `${VAR}` in the env file
4. Inline defaults from `workspace.yaml` (backward compatible — if no property reference, value is used as-is)

### Environment switching

```bash
# Dev (default — uses workspace.env.yaml)
swarmkit run my-swarm/ my-topology

# Production (uses workspace.env.prod.yaml)
SWARMKIT_ENV=prod swarmkit run my-swarm/ my-topology
```

## Non-goals

- Templating engine (no conditionals, loops, includes)
- Secrets management (env file is plain YAML — use vault/KMS for real secret rotation)
- Replacing `~/.swarmkit/config.yaml` — user-level settings (authoring model, global telemetry) stay there

## Backward compatibility

Existing workspaces with inline values in `workspace.yaml` continue to work unchanged. Property references (`${property.path}`) are only resolved if the `${}` syntax is present. No env file required.

## Implementation

### Changes required

1. **Resolver** — load `workspace.env.yaml` before resolving workspace, merge properties
2. **Interpolation engine** — two-phase: resolve properties from env file, then resolve `${ENV_VAR}` in property values
3. **Workspace schema** — no schema change needed. Property references are strings that the resolver interprets.
4. **`.gitignore` template** — `swarmkit init` adds `workspace.env*.yaml` to `.gitignore`
5. **CLI** — `SWARMKIT_ENV` env var support
6. **Validation** — `swarmkit validate` warns on unresolved `${property}` references

### Scope

- **Do:** property substitution, env file loading, env switching, gitignore
- **Don't:** secret rotation, vault integration, multi-file includes, conditional logic

## Test plan

- Unit: property resolution with nested paths
- Unit: env file loading with `${ENV_VAR}` interpolation
- Unit: missing property produces clear error
- Unit: backward compat — inline values still work
- Integration: `swarmkit run` with env file vs without
- Integration: `SWARMKIT_ENV=prod` picks up correct file

## Open questions

- Should `swarmkit init` generate a template `workspace.env.yaml` with placeholder values?
- Should there be a `swarmkit env` command to show resolved properties (for debugging)?
- Should property references support defaults? e.g., `${notifications.slack.channel:#general}`
