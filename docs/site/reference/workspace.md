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
| `memory` | Memory on by default: `enabled` (default `true`), `reader` (`max_results`, `similarity_threshold`, `search_scope`) and `writer` (`min_output_length`) for the automatically bound `memory-reader` / `memory-writer`; the `governed-memory` and `memory-reconcile` skills are bundled unless the workspace defines its own. `enabled: false` switches everything automatic off — [Workspace memory](workspace-memory.md). |
| `identity` | Human-identity provider (`builtin`/`auth0`/`okta`/`google`/`azure-ad`/`oidc`). |
| `model_providers` | Python-class registrations (`class`, `provider_id`, `config`) for a custom `ModelProvider`. The usual way to add a provider is a YAML file in `<workspace>/providers/` — see [Model provider](model-provider.md). |
| `credentials` | Named credential **references** (never literals): each `{ source, config }` where `source` is `env`, `file`, or `oauth` (a token obtained by logging in from the portal, stored encrypted per owner and refreshed before a run — see [Connections](connections.md)). The cloud sources (`hashicorp-vault`, `aws-secrets-manager`, `gcp-secret-manager`, `azure-key-vault`, `plugin`) are accepted by the schema and refused at resolution until a `SecretsProvider` is wired for them. |
| `mcp_servers` | The MCP registry: `id`, `transport` (`stdio`+`command` or `http`+`endpoint`), `env`, `credentials_ref`, `sandboxed`/`sandbox_image`, and governance `permission` tiers (`open`/`cautious`/`strict`/`readonly`). |
| `storage` | Backends for `checkpoints`, `audit`, `runtime` (jobs/conversations/usage), `artifacts`, `memory`, `fleet` and `knowledge_bases` (`default_backend: sqlite \| postgres`) — each `sqlite` or `postgres`, following `storage.runtime` unless they declare their own block ([Storage](storage.md)). `storage.artifacts` additionally takes `database_url` (override the inherited connection URL) or, for the `s3` backend, `bucket` (needs the `boto3` optional dependency). |
| `context_compression` | Opt-in read-side compression of bulk tool output: `backend` (`off` default / `columnar` / `headtail` / `plugin`, the last with `backend_class`), `min_bytes` (below which nothing is compressed), and `overrides[]` per surface — each with `match` (tool name glob) or `match_server` (glob on the backing MCP server id, e.g. `logs-*`) and its own `backend` / `min_bytes`. |
| `planning` / `synthesis` | Workspace-default planning and synthesis config, overridable per topology. |
| `events` | Where the runtime pushes what happened: `[{ sink: webhook \| stdout, url, credentials_ref, types }]`. Best-effort; `GET /events?after=<cursor>` is the durable log — see [Events](events.md). |
| `gates` | `auto_resume` (default `true`): a run continues as soon as its gate is resolved, so an application does not have to call `POST /jobs/{id}/resume` — turn it off to batch or delay. |
| `command_packs` | Local binaries exposed as `command` skills — the sibling of `mcp_servers` for capabilities that already exist as executables (`design/details/command-packs.md`). |
| `server` | `swarmkit serve` config: `jobs` (`max_concurrent`, `timeout_seconds`), `mcp.enabled`, `a2a` (`enabled`, `identity`), `canary` routes, and `auth`. |

### Serve authentication (`server.auth`)

`provider`: `none` (default; only safe on loopback) \| `api_key` \| `jwt`. A non-loopback bind with `provider: none` **refuses to start** unless `require_on_nonloopback: false` (default-secure). `api_key` needs `config.keys[]` (each `{ key_ref, client_id, client_name?, tier | scopes }` — `client_name` is the human-readable name shown in audit and `/whoami`, defaulting to the id); `jwt` needs `config.issuer` and reads scopes from the `scopes_claim` claim (default `scope`); `none` may set `identity` / `identity_name` so a loopback deployment still records who acted.

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
