# Workspace

A **workspace** is the top-level SwarmKit artifact (`kind: Workspace`, in `workspace.yaml`) that carries deployment-level configuration: identity, governance, model providers, credentials, the MCP server registry, storage backends, and serve-mode settings. Topologies, skills, archetypes, funnels, triggers, and the rest live *in* a workspace and inherit its configuration.

See the [workspace schema design note](https://github.com/delivstat/swarmkit/blob/main/design/details/workspace-schema-v1.md). This page is the artifact reference.

## Fields

Only `apiVersion`, `kind`, and `metadata` (`id` + `name`) are required; everything else is optional.

| Field | What it does |
|---|---|
| `metadata` | `id`, `name`, optional `description`, and free-form `annotations`. |
| `organisation` / `team` | Optional org/team identity (`{ id, name? }`). |
| `governance` | `provider` (`agt`/`mock`/`custom`), `policy_language` (`yaml`/`rego`/`cedar`), `limits` (circuit breakers: `max_steps_per_agent`, `max_steps_per_run`, `max_cost_per_run_usd`), and `decision_skills[]` inherited by all topologies. |
| `identity` | Human-identity provider (`builtin`/`auth0`/`okta`/`google`/`azure-ad`/`oidc`). |
| `model_providers` | Registrations (`class`, `provider_id`, `config`) — plug in a `ModelProvider`. |
| `credentials` | Named credential **references** (never literals): each `{ source, config }` where `source` is `env`/`file`/`hashicorp-vault`/`aws-secrets-manager`/`gcp-secret-manager`/`azure-key-vault`/`plugin`. |
| `mcp_servers` | The MCP registry: `id`, `transport` (`stdio`+`command` or `http`+`endpoint`), `env`, `credentials_ref`, `sandboxed`/`sandbox_image`, and governance `permission` tiers (`open`/`cautious`/`strict`/`readonly`). |
| `storage` | Backends for `checkpoints`, `audit`, `runtime` (jobs/conversations/usage), and `knowledge_bases` — each `sqlite` or `postgres`. |
| `context_compression` | Opt-in read-side compression of bulk tool output (`off` default / `columnar` / `headtail` / `plugin`). |
| `planning` / `synthesis` | Workspace-default planning and synthesis config, overridable per topology. |
| `server` | `swarmkit serve` config: `jobs` (`max_concurrent`, `timeout_seconds`), `mcp.enabled`, `canary` routes, and `auth`. |

### Serve authentication (`server.auth`)

`provider`: `none` (default; only safe on loopback) \| `api_key` \| `jwt`. A non-loopback bind with `provider: none` **refuses to start** unless `require_on_nonloopback: false` (default-secure). `api_key` needs `config.keys[]` (each `{ key_ref, client_id, tier | scopes }`); `jwt` needs `config.issuer`.

### Canary (`server.canary.routes`)

Each route splits one topology's traffic across ≥2 `versions` whose `weight` sums to 100, with optional `promote_when` criteria (`min_runs`, `error_rate_below`, `drift_below`, `window_minutes`).

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: my-swarm                   # required
  name: My Swarm                 # required
governance:
  provider: agt
  limits:
    max_steps_per_run: 500
    max_cost_per_run_usd: 25
model_providers:
  - class: swarmkit_runtime.model_providers.openrouter.OpenRouterProvider
    provider_id: openrouter
credentials:
  github:
    source: env
    config: { var: GITHUB_TOKEN }
mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    credentials_ref: github
    permission: cautious
storage:
  checkpoints: { backend: sqlite, path: .swarmkit/checkpoints.sqlite }
server:
  jobs: { max_concurrent: 5, timeout_seconds: 300 }
  auth:
    provider: api_key
    config:
      keys:
        - key_ref: env:SWARMKIT_API_KEY
          client_id: ci
          tier: run
```

## Authoring a workspace

`get_schema("workspace")` returns the full shape. Credentials are always references, never literal secrets. For the dev/staging/prod split and `${VAR}` interpolation, see [Environment configuration](env-config.md); for the memory subsystem, see [Workspace memory](workspace-memory.md).

## See also

- [Environment configuration](env-config.md) — `${property.path}` / `${VAR}` resolution across `workspace.yaml` and every artifact.
- [Workspace memory](workspace-memory.md) · [Serve mode](serve.md) · [Telemetry configuration](telemetry.md).
