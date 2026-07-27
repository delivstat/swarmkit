---
title: Bundled durable pipeline orchestrator + dispatch surface
description: Make the pipeline feature usable out of the box — ship a durable reference saga orchestrator as a separate `swarmkit orchestrator` application (store-mediated, touching neither the runtime core nor serve), durable on the SQLite/Postgres backend, replacing the example's in-memory controller, plus the CLI + serve-UI dispatch surface without which pipelines cannot be driven. Temporal stays the distributed-production swap.
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
- **Not putting the saga engine in the runtime *core* — nor in serve.** The orchestrator is a
  **separate application** (a `swarmkit orchestrator` command, peer to Temporal): neither the
  topology interpreter/compiler/governance *nor serve* imports the controller. Serve and the
  orchestrator communicate only through the durable store + the HTTP drive seam.

## The plan

### 1. Durable saga store — `SqlSagaStore` (the drop-in)

The reference controller already talks to a **`SagaStore` Protocol** (`get / create / save /
all_ids / seen / mark_seen`); `InMemorySagaStore` is just one implementation. So durability is a new
implementation, not a controller change:

- `SqlSagaStore(engine)` over SQLAlchemy Core, reusing `persistence._store.make_engine` (SQLite
  default, Postgres via the same URL seam — no new dependency, same tuning/WAL/psycopg handling).
- Three tables: `pipeline_saga` (one row per correlation_id: status, current_stages, passed_stages,
  pending_gate_stage, pending_lock_stage, attempts, timeline — JSON-encoded collections as `Text`,
  matching the persistence-store convention), `pipeline_saga_seen` (the dedup keys, append-only), and
  **`pipeline_events`** — the durable event queue that decouples serve from the orchestrator (see §3):
  `(id, correlation_id, event, status: queued|claimed|done, claimed_by, created_at)`.
- `save()` is a full-row upsert of the `SagaState`; `create()` inserts; `seen`/`mark_seen` hit the
  dedup table. State is written after every controller transition, so a restart resumes mid-saga.
- The event queue supports an **atomic claim** (mirroring the persistence store's `claim_queued`
  rowcount pattern) so the orchestrator can dequeue safely — the basis for the shared/Postgres tier.

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

### 3. A separate `swarmkit orchestrator` application (store-mediated)

Rather than embed the controller in serve, ship it as its **own long-running command**, mirroring
`swarmkit serve` — so the saga engine is a separate application that neither the runtime core nor
serve imports. Serve and the orchestrator communicate through the **durable store** (the event queue
+ saga state) plus the existing HTTP drive seam:

- **`swarmkit orchestrator <workspace> --serve-url <url> [--database-url <url>]`** — loads the
  workspace's resolved StageGraphs, opens the shared saga store, and runs the drive loop: claim
  queued `pipeline_events` → `controller.handle_event` → for each stage, HTTP `POST /pipelines/
  run-stage` on serve (governed, audited execution stays in serve) → persist saga state → mark the
  event done. It is the **only** process that imports `orchestration/reference/`.
- **Serve stays a thin authorize + read surface, and never decides the engine.** `POST
  /pipelines/signal` authorizes the event and hands it to the **injected `pipeline_signal` sink**
  (the existing domain-neutral seam) — it does *not* itself know or choose "store vs Temporal";
  `POST /pipelines/run-stage` executes a stage when an orchestrator calls it; `GET
  /pipelines/sagas[/{id}]` reads saga state for the CLI/UI. Serve never drives, never imports the
  controller.
- **Gate resolution is a signal too.** When a human approves a stage's funnel (via the review
  queue), that resolution is delivered as a `gate-resolved` event through the same sink; the
  orchestrator picks it up and resumes the saga. Uniform push, no in-process coupling, no poll loop.

### The `pipeline_signal` sink is the store-vs-Temporal switch (chosen at serve/workspace level)

The switch between the reference orchestrator and Temporal is **which sink serve is wired with** —
not any per-request logic in serve, and not a per-pipeline setting. The seam is unchanged
(`PipelineSignal(correlation_id, event)`); only the injected implementation differs:

| Orchestrator | `pipeline_signal` sink does… | Durable event log lives in |
|---|---|---|
| Reference (`swarmkit orchestrator`) | `INSERT` into `pipeline_events` (the store) | the SQLite/Postgres store |
| Temporal | call the Temporal client (start/signal a workflow) | Temporal's own history |

So with Temporal, **the `pipeline_events` table isn't used** — Temporal owns durability; serve's
sink just signals it. Serve imports neither the controller nor Temporal — it only calls the
callable it was handed at startup.

That wiring is a **deployment / serve-startup choice**, which in practice is **workspace level** (one
`swarmkit serve` = one workspace). The bundled default auto-wires the store sink (and you run
`swarmkit orchestrator`); swapping to Temporal means wiring the Temporal sink and running a Temporal
worker instead — `/pipelines/signal`, `/pipelines/run-stage`, the CLI, and the UI are all unchanged.
It is **not per-pipeline**: a deployment runs one orchestrator for all its pipelines. (Routing the
sink by pipeline id — some graphs to the store, some to Temporal — is possible but not worth the
complexity.)

This makes the reference controller and Temporal true **peers**: you pick your orchestrator by
wiring a sink + running the matching process. (A single-process convenience — the same controller
embedded in serve behind a flag, still store-mediated — is possible later, but the shipped default is
the clean separate app.)

**One read-side caveat.** `GET /pipelines/sagas[/{id}]` (and the CLI/UI it feeds) reads saga state
from the reference **store**. Under Temporal, saga state lives in Temporal, so that read view must
either query Temporal's API or read a projection Temporal writes back to the store. The *signal /
dispatch / run-stage* path is engine-agnostic; only the saga **read model** is store-specific, and
the Temporal swap owns providing its equivalent. Worth stating so the swap's scope is honest.

### 4. Dispatch + status surface (mandatory) — CLI + serve endpoints + UI

Two halves, both required for the feature to be usable: **dispatch** (start/feed/operate a pipeline)
and **status** (list, search, and inspect running + completed pipeline *instances*). The status half
does not exist today — the current Pipelines UI shows stage-graph *definitions* + gate coverage, not
live saga instances — and is exactly what a support operator needs to answer "what's running, and
where is correlation X?"

**Serve endpoints** (extend `_routes_pipelines`):
- `POST /pipelines/signal` (dispatch — exists); operator `advance` / `skip` already exist on it,
  reserved-scope guarded.
- `GET /pipelines/sagas?status=<active|completed|all>&graph=<id>&q=<substring>&limit=N` — the
  **searchable, filterable list of instances**: each row = correlation_id, stage-graph, **current
  status** (active / parked-on-gate / completed / rejected / failed), current stage, started/updated
  times. `q` matches the correlation_id (and, useful for support, the requirement/instance tag if the
  event carried one); `status` filters active vs completed.
- `GET /pipelines/sagas/{correlation_id}` — one instance in full, enough to **replay it on a
  read-only canvas**: the stage-graph shape (nodes + edges), each stage's **run state** (pending /
  active / completed / parked / rejected / failed), and per-node detail — the **input payload**, the
  **generated artifact(s)**, timing/attempts, and for a funnel node the **approval details** (the
  multi-party record: who / verdict / when / critique). This is assembled by joining the saga state,
  the **audit events** for the correlation_id (stage payloads/artifacts/timing), and the **approval
  records** — the saga store holds sequencing; the audit + review records hold the per-node evidence.
  Large artifacts are fetched **per node on selection** (a `GET /pipelines/sagas/{id}/node/{stage}`
  detail), not all inlined, so the list/replay stays light.

**CLI** `swarmkit pipeline`:
- `emit <graph> --input '{…}' [--correlation <id>]` — start/feed a pipeline (the primary trigger; a
  fresh correlation id starts a new saga). This is the `requirement`-wrapper's dispatch call.
- `sagas [--status active|completed|all] [--graph <id>] [<query>]` — the searchable list (query
  matches correlation_id); `status <correlation_id>` — full status + stage + gate + timeline.
- `advance` / `skip <correlation_id> <stage>` — operator acts (reserved scope), mirrors the ingress.

**Serve UI** — the **Pipelines** panel gains a **Runs** view beside the existing definitions view:
- a search box (by correlation_id) + status filter (Active / Completed / All) over a list of
  instances, each with its stage-graph, **status pill** (active / parked / completed / rejected /
  failed), and current stage;
- selecting a run opens a **read-only replay canvas** — the *existing* stage-graph canvas rendered in
  a run-state overlay (like the gate-coverage overlay): each node coloured by its state at that run,
  the parked gate marked, the taken path highlighted;
- **selecting a node / funnel / edge opens an inspector** showing, as available: the node's **input
  payload**, its **generated artifact(s)** (a diff, a report, a produced document — viewable/
  downloadable), timing/attempts, and for a funnel the **approval details** (who approved/rejected,
  when, the critique, the multi-party record), with a link to the gates/review panel to approve if
  it's parked;
- plus the transition **timeline** and a dispatch form.

Read + dispatch only — approvals happen on the one gates surface; the run canvas is for *examining*
state, payloads, artifacts, and approvals after the fact. It reuses `stage-graph-canvas` in a
read-only run mode, so it stays consistent with the definitions canvas.

**Serve UI** — a **Pipelines** panel: a dispatch form (pick a stage-graph, enter the JSON input,
emit), a live list of running sagas with their current stage + gate status, and a per-saga timeline.
Read + dispatch; gate *approval* stays on the existing gates/review panel (one approval surface).

### 5. docker-compose: orchestrator as a service, Temporal commented

The default compose runs **two SwarmKit services sharing the Postgres it already has**: `serve` (the
API/UI + enqueue/read) and `orchestrator` (`swarmkit orchestrator`, the drive loop). Both point at
the same `DATABASE_URL`; the orchestrator points `--serve-url` at the serve service. That's durable
pipelines from `docker compose up`. Ship a **commented `temporal` service (+ worker)** as the
documented swap: stop the reference `orchestrator` service, start Temporal — same serve, same store,
different driver. A short doc note states the tiers. (Local, non-compose: run `swarmkit serve` and
`swarmkit orchestrator` in two terminals against the same SQLite file.)

## Durability tiers

| Tier | Engine | Store | When |
|---|---|---|---|
| Default | `swarmkit orchestrator` (separate process) | SQLite | single-node, durable across restarts |
| Shared | `swarmkit orchestrator` (1+ processes) | Postgres | multi-node, one shared saga store + event queue |
| Production | Temporal (commented compose service) | Temporal | distributed, timers, signals, compensation at scale |

The user requirement — *durable, not in-memory* — is met at the default tier: SQLite persists saga
state, so a restart resumes mid-pipeline. In-memory becomes a test-only store, not a shipped default.

## API shape

- **`orchestration/reference/`**: the generic `PipelineController` core + `SqlSagaStore` + the
  `pipeline_saga` / `pipeline_saga_seen` / `pipeline_events` tables. `SagaStore` Protocol unchanged;
  `InMemorySagaStore` demoted to tests. Import-linter forbids the runtime core *and serve* from
  importing this package; only the `orchestrator` command may.
- **`swarmkit orchestrator`** (new CLI command): the drive loop over the store + serve's run-stage
  seam. The only importer of `orchestration/reference/`.
- **serve**: `POST /pipelines/signal` hands the event to the injected `pipeline_signal` sink (the
  store-writer by default; the Temporal client when swapped); `GET /pipelines/sagas[/{id}]` reads
  saga state; the review queue emits a `gate-resolved` signal on approval. No controller/Temporal
  import — serve calls the injected sink.
- **CLI**: `swarmkit pipeline emit|sagas|status|advance|skip` (over the serve endpoints).
- **UI**: `app/pipelines` gains a dispatch form + saga list/timeline (over the new endpoints).
- **compose**: `serve` + `orchestrator` services sharing Postgres; commented Temporal swap.

## Slices

1. `SqlSagaStore` + the `pipeline_saga` / `pipeline_saga_seen` / `pipeline_events` tables (with the
   atomic claim); unit tests (persist/resume, dedup, claim) against the in-memory store as the oracle.
2. Promote the generic controller core to `orchestration/reference/` (import-linter contract on the
   core *and* serve); the SDLC example composes onto it (its tests stay green).
3. Serve enqueue/read: `POST /pipelines/signal` → `pipeline_events`; `GET /pipelines/sagas` (list +
   filter/search) and `/{id}` (+ `/node/{stage}`) — the per-run detail assembled from saga state +
   audit events + approval records; the review queue writes a `gate-resolved` event on funnel approval.
4. `swarmkit orchestrator` command: the drive loop (claim events → drive saga → call run-stage →
   persist). Integration test: emit → stage runs → parks at gate → approve → resumes → completes,
   **with the orchestrator restarted mid-saga** (durability).
5. CLI `swarmkit pipeline emit|sagas|status|advance|skip` — including the searchable/filterable
   `sagas` list (by correlation_id + status) and `status <id>`; CLI ⇄ serve parity test.
6. UI Pipelines **Runs** view: search-by-correlation-id + Active/Completed/All filter over the
   instance list with status pills; a **read-only replay canvas** (reuse `stage-graph-canvas` with a
   run-state overlay) with a **node/funnel/edge inspector** (payload, generated artifacts, approval
   details, timeline); dispatch form. vitest for the pure bits (run-state → node styling, the
   selection→detail wiring).
7. docker-compose (`serve` + `orchestrator`) + commented Temporal + a docs note on the tiers; regen
   llms.

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

1. **Orchestrator ↔ serve auth.** The `orchestrator` process calls serve's `/pipelines/run-stage`
   and needs a credential (a service token). Confirm the mint/inject path (a reserved service
   identity), and whether the orchestrator reads the event queue directly from the shared DB (chosen)
   vs. a serve dequeue endpoint (rejected — reintroduces coupling).
2. **Event-queue claim on the shared (Postgres) tier.** Multiple `orchestrator` processes sharing one
   queue need an atomic claim on an event (and a saga) before driving it — mirror the persistence
   store's `claim_queued` rowcount pattern (+ the psycopg `rowcount=-1` gotcha). Single-node SQLite is
   unaffected; specify the Postgres row-lock before enabling the shared tier.
3. **Package boundary enforcement.** `orchestration/reference/` lives in the runtime package but must
   never be imported by the core **or serve** — enforce with an import-linter contract (like the
   governance/AGT rule) so the boundary can't silently erode; only the `orchestrator` command imports
   it.
4. **Local one-command ergonomics.** The shipped default is two processes (`serve` + `orchestrator`).
   Is a `swarmkit serve --with-orchestrator` convenience (spawns the drive loop in a serve worker
   thread, still store-mediated) worth it for local dev, or does it muddy the boundary enough to skip?
5. **Saga read model under Temporal.** The store-backed `GET /pipelines/sagas` read view is
   reference-specific; the Temporal swap must supply its equivalent (query Temporal, or project saga
   state back into the store so the same read view + CLI/UI work unchanged). Decide the expected
   contract for a swapped orchestrator so the read surface stays uniform.
6. **Where per-node artifacts live + retrieval.** The run inspector shows each node's generated
   artifact (a diff, a report). Are these read from the **audit log payload** (simple, but heavy for
   large artifacts and subject to any audit truncation), or from a dedicated artifact store keyed by
   `(correlation_id, stage)`? Decide the source + a size cap / lazy per-node fetch so the replay stays
   light, and confirm the audit-invariant read-only posture for the inspector.
