# 10 — Capability map (synthesis)

The bridge from inventory to design: for each platform capability, *what exists single-instance
today* → *what the control plane surfaces / aggregates / manages / versions*. This is the feature
list the control plane incorporates. Rows are grouped by the fleet proposal's three planes
(data / observability / control).

> Legend: **Have** = exists per-instance today · **Panel role** = the control-plane responsibility ·
> **Gap** = what's missing to get there.

## Control plane (manage)

| Capability | Have (today) | Panel role | Gap |
|---|---|---|---|
| Trigger a run | `POST /run/{topology}` (serve) | trigger on any instance; route; track | per-run identity + auth |
| Stop a run | — (`stop` is an M6 stub) | cancel a running job | implement stop |
| Artifact CRUD | `/api/{topologies,skills,archetypes}` validate→write→reload | central edit + push to instances | central **registry** + sync |
| Artifact versioning | topologies via canary `(name,version)`; skills/archetypes id-immutable | version + provenance for all artifact types | versioning for skills/archetypes; content hash |
| Canary / rollout | `CanaryRouter` + `/canary*` (in-memory, per-instance) | fleet-wide rollout + promotion policy | persist + aggregate canary metrics |
| Triggers / schedules | per-instance cron (`TriggerScheduler`, no dedup) + webhooks | fleet schedule view; dedup; central fire | distributed scheduler / single-owner |
| Workspace config | per-instance `workspace.yaml` | manage/push per-instance config | `server.auth` in schema; secret coordination |
| Conversational authoring | CLI `init`/`author`/`edit` (local) | front the authoring swarm in the panel | serve-exposed authoring + UI |
| Human approval (HITL) | `swarmkit review` + `FileReviewQueue` (per-instance) | fleet approval queue, identity-gated | aggregate queues; route approvals |

## Observability plane (measure / observe)

| Capability | Have (today) | Panel role | Gap |
|---|---|---|---|
| Traces | OTel spans + `RunTrace` JSON per instance | aggregate via OTel collector; link/embed | OTLP wiring per instance |
| Metrics | `swarmkit.*` counters/histograms | fleet dashboards (OTel-native) | — (use ecosystem) |
| Token usage / cost | `run_usage` + `UsageSummary` (cost ~0) | fleet usage/cost by model/provider/date | providers must emit `cost_usd` |
| Eval results | `swarmkit eval` → `.swarmkit/eval-results/` + `--compare` | fleet pass-rate trends + regressions | central eval-result store |
| Intent drift | M7 `drift/` + metrics | per-agent drift across fleet | aggregation |
| Audit | append-only SQLite per instance | **central** append-only merged log (compliance) | Postgres/central audit backend |
| Skill gaps | `gaps` + gap log + notifications | growth-loop input (gap → author → approve) | aggregation + loop wiring |
| Run explanation / Q&A | `why`, `ask` (LLM over local audit) | fleet-scoped `why`/`ask` | scope over aggregated audit |
| Prompts | local ring buffer (private) | respect privacy; opt-in only | — |

## Data plane (connect / store)

| Capability | Have (today) | Panel role | Gap |
|---|---|---|---|
| Instance connection | serve HTTP (REST + SSE), `auth/` seam | register/enroll/health instances | connector mode + registry |
| Transport auth | `AuthProvider` (None default / APIKey / JWT), `auth_middleware` | enforce panel↔serve auth, tiered | `server.auth` schema; tiers; default-secure |
| Human auth | `identity.provider` incl. OIDC (for governance) | human↔panel login (OIDC) | wire to panel session |
| Persisted state | per-instance SQLite + JSON under `.swarmkit/` | central aggregation or federated query | central stores / sync |
| Secrets | `credentials[]` references (env/vault/cloud) | coordinate central secret store (refs only) | per-instance secret resolution |
| MCP servers | per-workspace, lazy/boot start | fleet MCP registry/visibility | — |

## Sequencing the program keys off this map

1. **Auth hardening first** (control + data plane prerequisite): `server.auth` → schema; tiers +
   per-route scope; default-secure on non-loopback; reserved-scope deny-list; OIDC for human↔panel.
   *Mostly hardening an existing seam, not greenfield.*
2. **Connector + instance registry** (data plane): serve "connector mode" (register/health/
   capabilities) + panel registry.
3. **Aggregation** (observability): OTel-first for traces/metrics; central store for audit + eval +
   usage; respect prompt privacy.
4. **Artifact registry + versioning** (control): central versioned store + push/pull sync; extend
   versioning to skills/archetypes; record active version per run.
5. **Control-plane UI**: multi-instance dashboard reusing `lib/api.ts`; **conversational authoring**
   surface (the composer's missing half); fleet approval queue.
6. **Growth loop**: cross-instance skill-gap aggregation → author → human-approve.

## Hard constraints carried from the inventory

- Panel is **executive/media**, never legislative/judicial ([05](05-identity-governance-iam.md)):
  reserved-for-human scopes stay un-grantable; no `evaluate_action` bypass; audit append-only.
- **Don't rebuild generic observability** ([06](06-observability-eval.md)) — aggregate via OTel;
  build only the SwarmKit-specific signal views.
- **OSS vs commercial line** ([09](09-ui.md), `product-architecture.md`): the polished fleet panel
  is the Rynko surface; keep the OSS on-ramp (CLI + single-instance dashboard + serve) intact.
- **`eject` is an M9 stub** ([01](01-runtime-core.md), [08](08-cli.md)) — no policy-as-code export
  until it lands.
