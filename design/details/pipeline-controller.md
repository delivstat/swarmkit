# Pipeline controller + stage-graph (saga sequencing)

Parent: `design/details/sdlc-pipeline-example.md` (capability 3 of 5). This is the largest of the
five and the one that lives **outside** SwarmKit — it is the Minder split (`feedback_llm_language_
code_doing`) applied to a long-running pipeline: the application owns logic and state; SwarmKit
does determination + governance inside bounded stage runs.

The controller sequences a requirement across many stages over weeks. It holds durable
per-requirement state, reacts to real enterprise events, kicks the next SwarmKit run, owns the
integration-contract locks, and unwinds cleanly on cancellation. It is not an agent and not part of
any topology.

## Goal

Turn a pipeline definition into **data** (a stage-graph artifact) and run it as a **saga**: a
sequence of bounded SwarmKit stage runs correlated by `requirement_id`, advanced by external events,
serialised on shared-contract locks, and compensatable on cancel — with SwarmKit never asked to be a
weeks-long BPM engine.

## Non-goals

- **Not a SwarmKit runtime feature.** The controller is a reference component; it drives SwarmKit
  over stable serve APIs. It needs no eject story (it is not a run). It names the two small serve
  seams it depends on (below) rather than embedding itself in the runtime.
- **Not the gates.** Approval and the quality funnel are `multi-party-approval` / `gate-funnel`; the
  controller *waits on* a gate's resolution to advance, it does not implement the gate.
- **Not the board/queues.** Surfacing state to humans is `task-surface-and-board`; the controller is
  the state's source of truth, the board a view of it.
- **Not intra-stage rework.** A stage's internal revise→re-gate loop is inside that SwarmKit run
  (parent note); the controller only sees terminal stage outcomes.

## Where it lives

`examples/sdlc-pipeline/controller/` — a small self-contained service. The **stage-graph** is a
reusable artifact schema (canonical), so a different pipeline is new *data*, not new controller code
(topology-as-data spirit at the sequencing layer). Its persisted saga state is its own store
(SQLite/Postgres), separate from SwarmKit's.

## API shape

### Stage-graph (the pipeline as data)

```yaml
apiVersion: swarmkit/v1
kind: StageGraph
id: sdlc-pipeline
stages:
  - id: intake
    topology: sdlc/intake
    on: [requirement.created]                 # entry event(s)
    success: design.kickoff                    # signal emitted on clean completion
  - id: design
    topology: sdlc/design
    on: [design.kickoff]
    locks: [contract:oms-web, contract:oms-inventory]   # acquired before the run starts
    gate: consolidated-design-approval         # a gate-funnel gate (sibling); run parks on it
    success: design.approved                   # emitted when the gate approves
    release_locks_on: design.approved          # contract held through approval, then released
    compensation: sdlc/compensate-design       # topology to run if the requirement is cancelled
  - id: build
    topology: sdlc/build
    on: [design.approved]
    success: build.ready-in-qa
  - id: sit
    topology: sdlc/sit
    on: [build.ready-in-qa]                    # EXTERNAL event (CI) — not a SwarmKit signal
    success: sit.passed
loops:                                          # cross-stage edges (the defect cycle)
  - on: defect.raised -> defect-triage
  - on: defect.fixed  -> re-test
```

- `on` events are **external** enterprise events (Jira/CI/Git/SAST webhooks) *or* signals emitted by
  a prior stage's `success`. The controller treats both uniformly as inbound events.
- `gate` names a gate that the stage's SwarmKit run parks on; the controller learns the resolution
  via the gate-resolution seam (below) and emits the stage's `success` signal.

### Per-requirement saga state

For each `requirement_id` the controller persists: current stage(s), held locks, the pending gate,
per-stage attempt count, and status (`active | parked | failed | cancelled | done`). This is the
durable weeks-long state SwarmKit deliberately does *not* hold; every SwarmKit run stays bounded.

### Event model + idempotency

Inbound events (webhooks + stage signals) carry an **idempotency key** `(requirement_id, event,
source_event_id)`. The controller dedupes on it — external webhooks duplicate, arrive out of order,
and go missing, so a single delivery is never trusted:

- **Dedup:** a repeated key is a no-op.
- **Reconciliation:** on a timer the controller *pulls* source-system state (open PRs, build status,
  ticket transitions) and reconciles against its saga state, catching missed or dropped webhooks.
  Events are the fast path; reconciliation is the safety net.

### Integration-contract locking (decided: lock per requirement)

The controller is the **lock manager**. A stage's `locks` are acquired **before** its run starts and
released per `release_locks_on`:

- Locks are **per-contract** (per app-pair), not global — unrelated requirements run in parallel.
- A requirement needing several contracts acquires them **all-or-none in a fixed global order**
  (deadlock avoidance); if any is held, it queues without taking the others.
- A queued requirement waits (its saga is `parked`); the board shows held locks + the queue, and a
  lock carries the same SLA/escalation as a gate so a stuck hold is surfaced, not silent.
- Release is driven by the stage's `release_locks_on` signal (e.g. `design.approved`) — the contract
  is held across the whole consolidated-design→approval window, so no other requirement can commit a
  conflicting version underneath it.

### Failure vs wait

- A **wait** (parked on a gate or a lock) is cheap persisted state — no running process.
- A **stage-run failure** (harness crash, provider outage, MCP unreachable) is different: the
  controller **retries the stage run idempotently** (kicking the same topology with the same inputs
  is safe because the run is correlated and the KB reads are version-pinned). A stage that keeps
  failing is **surfaced to a human**, never silently dropped.

### Cancellation + compensation

A requirement can be withdrawn at any point. The controller marks the saga `cancelled`, releases its
locks, closes its open gate tasks, and runs each already-passed stage's `compensation` topology in
reverse order (revoke a draft, supersede KB entries, etc.). A saga must have an unwind path;
compensation is declared per stage in the graph.

### SwarmKit seams it depends on

The controller uses stable serve APIs, and needs two small additions (each a tiny, independently
useful runtime change — flagged, not built here):

1. **Run correlation label.** Kicking a topology must accept a `correlation_id` (= `requirement_id`)
   stamped on the run + its audit, so the append-only audit can be assembled *across* per-stage runs
   (the DORA view). Likely a small `serve` run-metadata addition.
2. **Gate-resolution notification.** When a gate resolves, the controller must learn of it — a
   webhook/callback from `serve` (or a poll of the gates API) so it can emit the stage's `success`.
   Reuses the existing review-gate state; adds a notification edge.

Everything else (kick a run, read run status, read the gate queue) already exists.

## Eject

Not applicable in the topology sense — the controller is not a run and produces no LangGraph. Its
*analogue* of an ejection story is that the stage-graph is plain data and the controller is small,
readable reference code: a real deployment can reimplement it against the same serve APIs. Invariant
7 is about runtime features; this is deliberately not one.

## Test plan

- **Stage-graph schema (Python + TS):** valid graphs parse; a stage referencing an unknown topology
  or an undefined lock/gate is rejected; a `loops` edge to an unknown stage is rejected.
- **Event dedup + ordering:** a duplicate `(requirement_id, event, source_event_id)` is a no-op;
  out-of-order events reach the correct stage; reconciliation repairs a *dropped* event (saga
  advances after a poll even though the webhook never arrived).
- **Locking:** two requirements needing the same contract serialise (second parks until release);
  requirements on disjoint contracts run in parallel; multi-contract acquisition is all-or-none in
  order (no deadlock under a crossed-need scenario).
- **Failure vs wait:** a parked saga consumes no run; a stage-run failure is retried idempotently and
  does not double-advance; repeated failure surfaces to a human.
- **Cancellation:** cancelling mid-pipeline releases locks, closes gate tasks, and runs passed
  stages' compensations in reverse.
- **Correlation:** every stage run for a requirement carries its `requirement_id`; the audit view
  assembles across them.
- **Defect loop:** `defect.raised` routes to `defect-triage`; `defect.fixed` routes to `re-test`;
  both correlate to the originating requirement.

## Demo plan

`just demo-pipeline-controller`: a mock event driver feeds one requirement through
intake → design (parks on the approval gate; a scripted approval advances it) → build → sit, then
injects (a) a duplicate webhook (no-op), (b) a *dropped* `build.ready-in-qa` that reconciliation
recovers, (c) a second concurrent requirement contending on the same contract (serialises), and
(d) a cancellation that unwinds with compensations. Prints the correlated saga timeline + audit.
Terminal transcript in the PR body.

## Schema-change checklist

Adds the `StageGraph` artifact schema — follow `docs/notes/schema-change-discipline.md`: canonical
JSON Schema, Python + TS validators, fixtures. The two serve seams (correlation label, gate-resolution
notification) are separate small runtime changes with their own tests, referenced from here.
