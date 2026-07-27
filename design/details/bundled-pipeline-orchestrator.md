---
title: Bundled durable pipeline orchestrator + dispatch surface
description: Make the pipeline feature usable out of the box — ship a durable reference saga orchestrator (SQLite/Postgres via the persistence seam) embedded in serve as an opt-out default, replacing the example's in-memory controller, and add the CLI + serve-UI dispatch surface without which pipelines cannot be driven. Temporal stays the distributed-production swap.
tags: [runtime, pipeline, orchestration, serve, cli, ui]
status: draft
---

# Bundled durable pipeline orchestrator + dispatch surface

**Scope:** `orchestration` (a shipped reference controller + a durable saga store), `serve` (embed +
dispatch endpoints), `cli` (`swarmkit pipeline`), `ui` (a Pipelines dispatch panel), `docker` (the
compose default). Additive; the domain-neutral drive contract is unchanged.
**Design reference:** builds on `orchestration-provider-seam.md` (the `RunStage` / `PipelineSignal`
drive contract) and `pipeline-controller.md` (the reference controller). §14 (runtime/serve) governs.
**Status:** draft (design-only) — review before implementation.

## The problem: the pipeline feature is inert out of the box

The runtime carries only the small, domain-neutral **drive seam** — `RunStage` (run one bounded
stage) and `PipelineSignal` (deliver one event) — and deliberately keeps the saga engine *out* of
the runtime, in `examples/sdlc-pipeline/orchestrator/`. That boundary is correct, but it has a
consequence: a fresh `swarmkit serve` exposes `/pipelines/*` that **503 until a deployer wires an
orchestrator into `app.state`**, and the only working one is example code you must copy. So "SwarmKit
has pipelines" is true of the *feature* and false of the *distribution*: `docker compose up` gives
you dead pipeline endpoints. This note closes that gap without dragging saga/Temporal code into the
runtime *library*.

Two additional decisions from review:

- **Durable by default.** The reference controller is in-memory (`InMemorySagaStore`) — it loses
  saga state on restart, which is the wrong default for a bundled component. We already run
  SQLite/Postgres for persistence and governed memory; the saga store should ride the same backend.
- **Dispatch is mandatory.** A bundled orchestrator with no way to *start* a pipeline is still
  unusable. The CLI and serve UI must provide event dispatch (emit / status / list / operator
  advance-skip). Without it, "usable" is not met.

## Non-goals

- **Not replacing Temporal.** The bundled controller is the single-node durable default; Temporal
  remains the distributed/production `OrchestrationProvider`, shipped commented-out in compose.
- **Not changing the drive contract.** `RunStage` / `PipelineSignal` / `StageOutcome` are unchanged.
- **Not putting the saga engine in the runtime *core*.** The topology interpreter / compiler /
  governance never import the controller. It is a **serve-application** component (like the job store
  and review queue already are), imported only by serve's optional pipeline embed.

## The plan

### 1. Durable saga store — `SqlSagaStore` (the drop-in)

The reference controller already talks to a **`SagaStore` Protocol** (`get / create / save /
all_ids / seen / mark_seen`); `InMemorySagaStore` is just one implementation. So durability is a new
implementation, not a controller change:

- `SqlSagaStore(engine)` over SQLAlchemy Core, reusing `persistence._store.make_engine` (SQLite
  default, Postgres via the same URL seam — no new dependency, same tuning/WAL/psycopg handling).
- Two tables: `pipeline_saga` (one row per correlation_id: status, current_stages, passed_stages,
  pending_gate_stage, pending_lock_stage, attempts, timeline — JSON-encoded collections as `Text`,
  matching the persistence-store convention) and `pipeline_saga_seen` (the dedup keys, append-only).
- `save()` is a full-row upsert of the `SagaState`; `create()` inserts; `seen`/`mark_seen` hit the
  dedup table. State is written after every controller transition, so a restart resumes mid-saga.

The controller is constructed `PipelineController(run_stage=…, store=SqlSagaStore(engine))` — a
one-line swap from the in-memory default.

### 2. Promote the *generic* controller core (not the SDLC bits)

The example controller mixes a **domain-neutral saga engine** (graph traversal, saga state, the
run-stage loop, gate-wait, bounded retry, dedup) with **SDLC-specific** pieces (`SourceStateProvider`,
contract `LockManager`, `SurfaceNotice`). Promote only the generic engine:

- Ship the generic core + `SqlSagaStore` under `swarmkit_runtime/orchestration/reference/` — part of
  the package but **not imported by the runtime core**; only serve's embed imports it.
- The SDLC-specific pieces stay in `examples/sdlc-pipeline/` and compose *onto* the core (the example
  becomes a thin domain layer over the shipped engine, proving the extension seam).
- The stage sequence comes from the resolved **StageGraph** artifacts already in the workspace, so a
  new pipeline stays *data*, not controller code.

### 3. Embed in serve (opt-out default)

`swarmkit serve` gains a `--pipelines / --no-pipelines` flag (default **on**): when on, serve
constructs the durable controller over its own DB and wires both seams —
`app.state.pipeline_run_stage` (the `StageRunner` over a run context, the existing production path)
and `app.state.pipeline_signal` (the controller's `handle_event`), and registers the controller's
gate-resolution against the review queue so an approved funnel advances the saga. `--no-pipelines`
restores today's behaviour (endpoints 503) for deployers who bring their own engine. The runtime
*library* still never imports the controller — only this serve wiring does.

### 4. Dispatch surface (mandatory) — CLI + serve endpoints + UI

**Serve endpoints** (extend `_routes_pipelines`): `POST /pipelines/signal` (emit — exists) plus read
surfaces `GET /pipelines/sagas` (list instances + status) and `GET /pipelines/sagas/{correlation_id}`
(one saga's stage/gate/timeline). The operator `advance`/`skip` modes already exist on `signal`,
guarded by the reserved human-identity scope.

**CLI** `swarmkit pipeline`:
- `emit <graph> --input '{…}' [--correlation <id>]` — start/feed a pipeline (the primary trigger; a
  fresh correlation id starts a new saga). This is the `requirement`-wrapper's dispatch call.
- `sagas` / `status <correlation_id>` — list instances / inspect one (stage, gate, timeline).
- `advance` / `skip <correlation_id> <stage>` — operator acts (reserved scope), mirrors the ingress.

**Serve UI** — a **Pipelines** panel: a dispatch form (pick a stage-graph, enter the JSON input,
emit), a live list of running sagas with their current stage + gate status, and a per-saga timeline.
Read + dispatch; gate *approval* stays on the existing gates/review panel (one approval surface).

### 5. docker-compose: durable by default, Temporal commented

Because the controller is embedded in serve, the default compose needs **no extra service** — `serve`
with pipelines on + the Postgres it already runs = durable pipelines out of the box. Ship a
**commented `temporal` service + the Temporal adapter wiring** as the documented swap for
distributed/production durability. A short doc note states the tiers.

## Durability tiers

| Tier | Engine | Store | When |
|---|---|---|---|
| Default | embedded reference controller (in serve) | SQLite | single-node, durable across restarts |
| Shared | embedded reference controller | Postgres | multi-serve, one shared saga store |
| Production | Temporal (commented compose service) | Temporal | distributed, timers, signals, compensation at scale |

The user requirement — *durable, not in-memory* — is met at the default tier: SQLite persists saga
state, so a restart resumes mid-pipeline. In-memory becomes a test-only store, not a shipped default.

## API shape

- **`orchestration/reference/`**: the generic `PipelineController` core + `SqlSagaStore` +
  `saga` tables. `SagaStore` Protocol unchanged; `InMemorySagaStore` demoted to tests.
- **serve**: `--pipelines` default-on embed; `GET /pipelines/sagas[/{id}]`; controller wired to the
  review queue for gate resolution.
- **CLI**: `swarmkit pipeline emit|sagas|status|advance|skip`.
- **UI**: `app/pipelines` gains a dispatch form + saga list/timeline (over the new endpoints).
- **compose**: pipelines-on serve; commented Temporal.

## Slices

1. `SqlSagaStore` + the `pipeline_saga` / `pipeline_saga_seen` tables; unit tests (persist/resume,
   dedup) against the in-memory store's behaviour as the oracle.
2. Promote the generic controller core to `orchestration/reference/`; the SDLC example composes onto
   it (its tests stay green).
3. Serve embed (`--pipelines` default-on) + the durable controller wired to run-stage + review-queue;
   `GET /pipelines/sagas[/{id}]`. Integration test: emit → stage runs → parks at gate → approve →
   advances → completes, surviving a store round-trip.
4. CLI `swarmkit pipeline emit|sagas|status|advance|skip` over the endpoints; CLI ⇄ serve parity test.
5. UI Pipelines dispatch panel (form + saga list + timeline); vitest for the pure bits.
6. docker-compose default + commented Temporal + a docs note on the tiers; regen llms.

## Test plan

- **Unit:** `SqlSagaStore` persist/resume/dedup parity with `InMemorySagaStore`; the generic core
  drives a scripted stage-graph identically under either store.
- **Integration:** a full emit → run-stage → park → approve → advance → complete cycle through serve
  with the durable store, then reconstruct the same saga from a fresh store instance (restart
  survival).
- **Parity:** `swarmkit pipeline sagas/status` and `GET /pipelines/sagas` return the same JSON.
- **Example:** the SDLC example's controller tests stay green after composing onto the promoted core.

## Demo plan

`just demo-pipeline-durable`: emit a two-stage pipeline, show it park at the gate, **restart the
store** (new `SqlSagaStore` on the same SQLite file), resolve the gate, watch it resume and complete
— proving durability. Plus `swarmkit pipeline sagas` output.

## Open questions

1. **Gate resolution wiring.** The embedded controller learns a gate result via the review queue
   (an approved funnel → `resolve_gate`). Confirm the cleanest hook: a review-queue callback vs the
   controller polling `gate-status`. Leaning callback (push) to avoid a poll loop in-process.
2. **Concurrency on the shared (Postgres) tier.** Multiple serves sharing one saga store need a
   claim/lock on a saga before advancing it (mirror the persistence store's `claim_queued`
   atomicity). Single-node SQLite is unaffected; specify the Postgres row-lock before enabling the
   shared tier.
3. **Package boundary.** `orchestration/reference/` lives in the runtime package but must never be
   imported by the core — enforce with an import-linter contract (like the governance/AGT rule) so
   the boundary can't silently erode.
