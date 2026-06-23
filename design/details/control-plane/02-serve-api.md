# 02 — Serve HTTP API + automation (the connector contract)

Scope: `packages/runtime/src/swarmkit_runtime/server.py` (FastAPI), `triggers/`, `canary/`,
`auth/`. This is the contract a control plane talks to, so it is the most detailed doc.

`create_app(workspace_path, *, cors_origins=None, auth_provider=None)` builds the app
(`server.py:1172`). Boot (lifespan, ~1183–1231): load `WorkspaceRuntime`, create `SqliteStore`,
parse `server` config (`jobs.max_concurrent`=5, `jobs.timeout_seconds`=300, `mcp.enabled`=true),
job semaphore, boot MCP, start `TriggerScheduler` (30s poll), init `CanaryRouter`. Middleware:
CORS (default `allow_origins=["*"]`), request logging, **auth** (below). CLI `swarmkit serve`
defaults **`--host 0.0.0.0 --port 8000`**.

## Routes (current)

| Method · Path | Purpose | Notes |
|---|---|---|
| GET `/health` | liveness + workspace id | **auth-exempt** |
| GET `/topologies` | list topology names (+ `name@version`) | |
| GET `/skills` | `[{id, category}]` | |
| GET `/archetypes` | list ids | |
| GET `/validate` | full workspace validation report | |
| GET `/triggers` | trigger configs (secrets not exposed) | |
| GET `/usage` , `/usage/{job_id}` | token usage summary (from store) | cost fields exist, mostly 0 |
| GET `/jobs` | in-memory jobs | |
| GET `/jobs/history` | persisted jobs (≤100) | from SqliteStore |
| GET `/jobs/{id}` | poll `{job_id,status,output,error}` | |
| GET `/jobs/{id}/stream` | **SSE** progress; `data: [done] status=…` terminator | |
| POST `/run/{topology}` | start async job → `{job_id,status}` | canary-routed; `429` if semaphore full |
| POST `/hooks/{topology}` | webhook trigger | + HMAC signature check (below) |
| GET `/canary` , POST `/canary/{t}/promote` , POST `/canary/{t}/rollback` | canary status + manual control | |
| POST `/conversations` , GET `/conversations[/{id}]` , POST `/conversations/{id}/messages` (SSE) | multi-turn sessions | |
| GET `/api/topologies/{id}` , `/api/topologies/{id}/yaml` , `/api/skills/{id}[/yaml]` , `/api/archetypes/{id}[/yaml]` | read artifact + resolved tree | |
| PUT `/api/topologies/{id}` , `/api/skills/{id}` , `/api/archetypes/{id}` | edit YAML → validate → write → re-resolve; `dry_run` supported | |
| POST `/api/topologies` , DELETE `/api/topologies/{id}` , POST `/api/reload` | create / delete / reload | |
| GET·POST `/mcp` | MCP transport (each topology a tool) — only if `mcp` installed | |

Job execution: `execute_job()` runs under the semaphore + timeout; on completion records canary
metrics. In-memory `JobStore` (`Job`: id/topology/status/input/version/output/error/events/timestamps)
+ optional SqliteStore persistence.

## Auth (the seam — exists, defaults open, unschematized)

- **Module:** `auth/` — `AuthProvider` ABC (`_provider.py`), `NoneAuthProvider` (`_none.py`,
  grants `{"*"}`), `APIKeyAuthProvider` (`_api_key.py`, `Authorization: Bearer`), `JWTAuthProvider`
  (`_jwt.py`, JWKS auto-discovery, claims→scopes), plus a `_registry.py`.
- **Wiring:** `server.py:1180` `_auth = auth_provider or NoneAuthProvider()`; `auth_middleware`
  (`1264`) calls `_auth.authenticate(AuthRequest{headers,path,method,query,client_ip})`, attaches
  `request.state.identity`, returns `401` JSON on `AuthError`. `/health` is exempt.
- **Selection:** CLI `_build_auth_provider(workspace_path)` (`cli/__init__.py:2037`) reads a
  **`server.auth`** block → `none` | `api_key` (`keys`) | `jwt` (issuer/audience/jwks).
- **Gaps (control-plane critical):**
  - **`server.auth` is NOT in `workspace.schema.json`** — undocumented, unvalidated config.
  - Default is `NoneAuthProvider` + bind `0.0.0.0:8000` → **open by default**.
  - No scope **tiers** (read vs run vs admin) — `NoneAuthProvider` grants wildcard; the providers
    extract scopes but routes don't enforce per-route scope requirements.
  - CORS defaults to `*`.
  - Webhook HMAC (below) is **orthogonal** to identity auth and doesn't bind a client id.

## Triggers / schedules

Trigger artifact (`kind: Trigger`): `type` ∈ `cron|webhook|file_watch|manual|plugin`, `enabled`,
`targets` (topology ids, fired in parallel), `config` (type-specific), `provider_id` (plugin).
- **cron** — `TriggerScheduler` (`triggers/_scheduler.py`) polls every 30s, `croniter` computes next
  fire, calls `_fire_trigger(topology, "trigger:<id>")` under the semaphore. **Each instance
  schedules independently — no cross-instance dedup.**
- **webhook** — `POST /hooks/{topology}`; HMAC-SHA256 via `triggers/_webhook.py`, secret from
  `config.auth.credentials_ref` (env), header default `X-Hub-Signature-256`. `401` on mismatch.
- file_watch / manual / plugin — config present; manual fired via CLI/UI.

## Canary

`server.canary.routes[]` → `(topology, versions[{version, weight, promote_when}])`. `CanaryRouter`
(`canary/_router.py`): weighted `select()`, `record_result()` updates `VersionMetrics`
(total/failed/drift, windowed), `_check_promotion()` (min_runs, error_rate_below, drift_below,
window_minutes) auto-promotes to 100%. `promote()`/`rollback()` manual. **Metrics are in-memory,
reset on restart, per-instance.**

## Control-plane implications

- **Connector verbs the panel calls:** `POST /run`, `GET /jobs/{id}[/stream]`, `/validate`,
  `/topologies|skills|archetypes`, the `/api/*` CRUD (artifact management), `/usage`, `/canary*`,
  `/triggers`. The contract is already REST + SSE and complete enough to drive remotely.
- **Auth is the prerequisite and is ~80% present.** Work: (1) add `server.auth` to the schema;
  (2) make a non-loopback bind require a non-None provider (or warn loudly); (3) add scope **tiers**
  + per-route scope enforcement; (4) panel↔serve = bearer/JWT (exists), human↔panel = OIDC
  (separate edge). See [05](05-identity-governance-iam.md) for the separation-of-powers constraints.
- **Multi-instance gaps:** in-memory job store + canary metrics + per-instance cron scheduling (no
  dedup) + per-instance SQLite ([04](04-persistence-state.md)). The panel must own cross-instance
  aggregation; scheduled triggers need a distributed-lock or single-scheduler model.
- **The `/api/*` CRUD already lets the panel manage artifacts remotely** (validate→write→reload),
  which is the basis for a central artifact registry + push-to-instance sync ([07](07-schema.md)).
