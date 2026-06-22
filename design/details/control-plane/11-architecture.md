# 11 — Phase 1: Control plane architecture

Builds on the Phase 0 inventory ([00](00-index.md)–[10](10-capability-map.md)). Defines what the
control plane *is*, its components, the multi-instance + connection model, the data model, and the
trust boundaries — enough to design auth (Phase 2) and build the connector/registry (Phase 3+)
against. Implementation specifics are deferred to per-phase design notes.

> **Status:** Phase 1 draft. Contains **Decisions needed from you** (below) that shape everything
> downstream — these are called out rather than silently assumed.

## 1. Goal

A self-hostable plane that **connects to, observes, and manages multiple SwarmKit instances** from
one place: a versioned artifact registry, cross-instance run/trace/eval/audit aggregation, a fleet
approval queue, and (eventually) conversational authoring. Each instance keeps running
independently; `serve` becomes a thin authenticated **connector**.

### Non-goals (explicit)

- **Not** a new execution engine — runs still happen on instances via the existing runtime; the
  panel orchestrates, it does not execute.
- **Not** a generic trace/metrics UI — aggregate via the OTel ecosystem ([06](06-observability-eval.md));
  build only SwarmKit-specific signal views.
- **Not** a bypass of governance — the panel is executive/media, never legislative/judicial
  ([05](05-identity-governance-iam.md)).
- **Not** a secrets store — secrets stay *references*; the panel coordinates, never holds values.

## 2. Decisions needed from you (load-bearing)

| # | Decision | Recommendation | Why it matters |
|---|---|---|---|
| D1 | **OSS ↔ commercial cut** | **Self-hostable OSS core** (registry + aggregation + connector + token auth + minimal fleet UI in this repo); commercial Rynko = hosted multi-tenant, teams/SSO at scale, advanced analytics, SLA. | Determines what lives in this repo vs Rynko, and whether the fleet idea is paywalled. The #356 proposal said "self-hostable" — but `product-architecture.md` carves UI/cloud/teams to Rynko. Genuinely your call. |
| D2 | **Connection model** | **Hybrid**: panel→instance **pull** for control (REST over the existing serve API, request/response, easy auth); instance→collector **push** for observability (OTLP) + lightweight instance→panel **heartbeat**. | Pull keeps control simple + auditable and reuses serve as-is; push fits high-volume telemetry. Pure-push control would mean inventing a reverse channel; pure-pull telemetry doesn't scale. |
| D3 | **Aggregation store** | **OTel collector** for traces/metrics; **central Postgres** for audit + eval results + usage + registry metadata; federated live-query (call serve) for current jobs. | Avoids a giant central data lake while keeping compliance-grade audit + reproducible artifacts central. |
| D4 | **UI** | **Two surfaces**: keep the existing Next.js app as the **single-instance OSS dashboard**; build the **fleet panel** as a distinct app reusing `lib/api.ts` patterns + `@swarmkit/schema`. | The existing UI is single-instance/zero-auth ([09](09-ui.md)); retrofitting tenancy/auth/instance-selection into it is messier than a clean panel that talks to the control-plane API. |

The rest of this doc assumes the recommended answers; flag changes and I'll revise.

## 3. The three planes (concretized)

```
                ┌─────────────────────── Control Plane ───────────────────────┐
   human ──OIDC──▶  Fleet UI  ──▶  Control-Plane API                            │
                │                    ├─ Instance registry (enroll/health/caps)  │
                │                    ├─ Artifact registry + versioning          │
                │                    ├─ Orchestration (trigger/cancel/CRUD)      │
                │                    ├─ Approval queue (fleet HITL)              │
                │                    └─ Aggregation API (audit/eval/usage)       │
                └───────┬───────────────────────┬───────────────────────────────┘
        control (pull, token)            observability (push)
                        │                        │
        ┌───────────────▼────────┐      ┌────────▼────────────┐
        │  serve (connector)     │      │  OTel Collector      │
        │  + AuthProvider        │ ◀────│  (traces/metrics)    │
        │  + connector endpoints │      └─────────────────────┘
        │  per-instance runtime  │
        └────────────────────────┘   × N instances (Minder, Sterling, vedanta, …)
```

- **Control plane** (the new service): registry, artifact registry, orchestration, approval queue,
  aggregation API, human auth (OIDC). Backed by central Postgres + the artifact registry.
- **Observability plane**: instances export OTLP to a collector; the panel links/embeds for raw
  traces and pulls SwarmKit-specific signals (eval/drift/governance/gaps) into the central store.
- **Data plane**: the **connector** = `serve` hardened with auth ([02](02-serve-api.md) §Auth,
  Phase 2) + a few connector endpoints; per-instance state stays local, surfaced on demand.

## 4. Multi-instance model

- **Instance** = one `serve` deployment (a workspace + runtime at an endpoint). Identified by a
  control-plane-assigned `instance_id`.
- **Enrollment**: operator registers an instance (endpoint + a per-instance **enrollment token**).
  The panel verifies by calling `GET /health` + a new `GET /capabilities` (advertises: schema
  version, model providers/models available, topologies, governance provider, feature flags).
  Token is minted by the panel and configured on the instance (`server.auth`, Phase 2).
- **Connection (D2 hybrid)**:
  - *Control*: panel → instance over the existing REST API (`/run`, `/jobs`, `/api/*`, `/canary*`,
    `/validate`) with a bearer token. Request/response, fully auditable.
  - *Observability*: instance → OTel collector (OTLP push) for traces/metrics; instance → panel
    **heartbeat** (periodic `POST /instances/{id}/heartbeat` with status + cheap counters) so the
    panel knows liveness without polling every instance.
  - *Pull-on-demand*: for current jobs/usage the panel calls the instance's serve directly (live).
- **Health**: heartbeat + on-demand `/health`; instances marked stale after N missed heartbeats.
- **Scheduling caveat** ([02](02-serve-api.md)): each instance schedules its own cron independently
  (no dedup). For panel-owned schedules, the panel fires via `/run`; instance-local cron stays
  local. Cross-instance dedup is a later concern, not v1 of the panel.

## 5. Control-plane data model (entities)

| Entity | Key | Holds | Source |
|---|---|---|---|
| **Instance** | `instance_id` | endpoint, enrollment token ref, schema version, capabilities, health, last_heartbeat | registry |
| **Artifact** | `(kind, id)` | latest + history pointer; kind ∈ topology/skill/archetype/workspace/trigger | registry |
| **ArtifactVersion** | `(kind, id, version, content_hash)` | YAML, provenance, author, created_at, validated-against schema version | registry |
| **Deployment** | `(instance_id, kind, id)` → version | which artifact version is active on which instance | registry |
| **Run** (ref) | `(instance_id, run_id)` | status, topology+version, timing, usage, link to trace | pulled/heartbeat |
| **AuditEntry** | `event_id` (global) | the AuditEvent shape ([04](04-persistence-state.md)) + `instance_id` | central append-only |
| **EvalResult** | `(instance_id, eval_set_id, ts)` | pass rate, regressions, case verdicts | central |
| **ApprovalItem** | `approval_id` | instance_id, run_id, artifact/skill, verdict, status, acting identity | fleet queue |
| **Identity / Token** | `client_id` / `token_id` | OIDC subject (humans) or token (panel↔instance) + scope tier | auth (Phase 2) |

Notes: artifact versioning is **net-new** for skills/archetypes/workspace ([07](07-schema.md));
topologies already have canary `(name, version)` which the registry subsumes. `content_hash` enables
"replay with version lock" and drift detection between registry and instance.

## 6. Trust & boundaries (carried from inventory)

- **Separation of powers** ([05](05-identity-governance-iam.md)): the panel is **executive/media**.
  It may trigger runs, read/aggregate audit, manage artifacts (subject to gates), and surface
  approvals — it may **not** be granted reserved-for-human scopes (`skills:activate`,
  `mcp_servers:deploy`, `topologies:modify`, `iam:modify`), bypass `evaluate_action`, or modify
  audit. Recommend a **hardcoded reserved-scope deny-list** in the auth-hardening phase.
- **Two auth edges** (Phase 2): human↔panel = **OIDC**; panel↔instance = **token tiers**
  (read / run / admin). Transport authn ≠ governance identity ≠ agent identity — three layers.
- **Artifact mutation is legislative**: pushing a topology to an instance changes its rules.
  The registry→instance push must itself be a governed, audited, human-gated action (not an
  ambient panel capability).
- **Secrets** ([03](03-provider-seams.md)): the panel coordinates references + a central secret
  store; it never transports secret values; instances resolve via their `SecretsProvider`.

## 7. What each later phase builds (mapped to the program)

- **Phase 2 — Auth design** (next): the two-edge model + token tiers + reserved-scope deny-list,
  **and folds in the concrete `server.auth` spec** (schematize the existing seam: add `server.auth`
  to `workspace.schema.json`, default-secure on non-loopback bind, per-route scope enforcement).
  Resolves the [02 §Auth](02-serve-api.md) gaps.
- **Phase 3 — Connector + registry**: serve `/capabilities` + heartbeat; panel instance registry;
  enrollment flow.
- **Phase 4 — Aggregation**: OTLP collector wiring; central audit + eval + usage stores; the
  aggregation API.
- **Phase 5 — Artifact registry + versioning**: central versioned store + governed push/pull sync;
  extend versioning to skills/archetypes/workspace.
- **Phase 6 — Fleet UI**; **Phase 7 — growth loop**; **Phase 8 — hardening/rollout**.

## 8. Risks & open questions

- **Cost analytics blocked** until ModelProviders emit `cost_usd` ([03](03-provider-seams.md),
  [06](06-observability-eval.md)) — a prerequisite for fleet cost dashboards.
- **`eject` (M9 stub)** — no policy/topology "as code" export until it lands ([01](01-runtime-core.md)).
- **Schema-version skew** across instances ([07](07-schema.md)) — the registry must refuse to push
  artifacts an instance's `swarmkit-schema` can't validate; needs a compatibility matrix.
- **Push-schedule dedup** deferred — panel-owned schedules avoid it; instance-local cron stays local.
- **D1 (OSS/commercial)** gates how much of §3–§5 is built in this repo vs Rynko.
