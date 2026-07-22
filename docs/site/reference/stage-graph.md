# StageGraph

A **stage graph** is a first-class SwarmKit artifact (`kind: StageGraph`) that expresses a whole pipeline as data. It is an ordered set of bounded `stages`, plus optional cross-stage `loops`, that a **controller** sequences as a **saga**: a long-running process correlated by `requirement_id`, advanced by external enterprise events (Jira / CI / Git / SAST) or signals emitted by a prior stage, serialised on shared-contract locks, and compensatable on cancel. Each stage kicks a SwarmKit topology run, so a different pipeline is new *data*, not new controller code — topology-as-data applied at the sequencing layer.

The saga model, the controller's durable state, event dedup + reconciliation, contract locking, and compensation are specified in the design note: [pipeline controller](https://github.com/delivstat/swarmkit/blob/main/design/details/pipeline-controller.md) (`design/details/pipeline-controller.md`). This page is the artifact reference.

## The runtime runs stages; the controller runs the saga

The single most important thing about a stage graph: **the SwarmKit runtime does not execute it.** SwarmKit runs the bounded *topology* inside each stage and nothing more. A separate **controller** — a reference component under `examples/sdlc-pipeline/controller/`, not part of the runtime and not an agent in any topology — reads the published stage graph and owns everything long-running:

- the durable per-requirement saga state (current stage(s), held locks, the pending gate, per-stage attempt count, and status: `active | parked | failed | cancelled | done`);
- reacting to inbound events with an idempotency key `(requirement_id, event, source_event_id)` — dedup for duplicates, and a reconciliation timer that pulls source-system state to recover dropped webhooks;
- integration-contract locking (it is the lock manager);
- kicking the next SwarmKit run and unwinding cleanly on cancellation.

SwarmKit is deliberately never asked to be a weeks-long BPM engine. This mirrors the Minder split — the application owns logic and state; SwarmKit does determination + governance inside bounded runs.

## Authored as data, referenced by id

A stage graph lives in a `pipelines/` directory in the workspace. Its stage fields reference other artifacts **by id**: `topology` and `compensation` name topologies, and `gate` names a [Funnel](funnel.md). Defining the pipeline as one artifact — rather than wiring the sequence into code — is what lets the same controller run any pipeline, and lets the composer `ref`-validate the graph against the workspace before it is published. Editing the pipeline is a `topologies:modify`-class act, so it is routed through the growth-loop proposal → approval path; the controller reads only the published version.

## Stage fields

Each entry in `stages[]` describes one bounded run and how the saga enters and leaves it. Stage ids must be unique.

| Field | Required | What it does |
|---|---|---|
| `id` | yes | Unique lowercase-kebab stage id. |
| `topology` | yes | The topology this stage kicks as a bounded SwarmKit run. |
| `when` | no | Entry event(s). The stage starts when **one** of these arrives — an external enterprise event or a prior stage's `success` signal. The controller treats both uniformly. |
| `success` | no | The signal emitted on clean stage completion; it drives the next stage's `when` (and can release locks via `release_locks_on`). |
| `locks` | no | Ids that reference [Contract](contract.md) artifacts by id — the integration contracts this stage holds, acquired **all-or-none, in a fixed global order**, *before* the run starts (deadlock avoidance; unrelated requirements on disjoint contracts still run in parallel). The resolver ref-checks each lock against the workspace's contracts; an unknown contract is rejected. |
| `release_locks_on` | no | The signal whose arrival releases this stage's locks — e.g. hold a contract through approval, then release on `design.approved`. |
| `gate` | no | A `Funnel` id the stage's run parks on. The controller learns the resolution via the gate-resolution seam and emits the stage's `success`. |
| `compensation` | no | A topology run that unwinds this stage if the requirement is cancelled *after* the stage passed (revoke a draft, supersede KB entries, etc.). |

## Loops (the defect cycle)

Top-level `loops[]` are cross-stage edges. Each `{when, to}` routes an inbound event to a stage id (which must be a stage in this graph): `defect.raised → defect-triage`, `defect.fixed → re-test`. Both correlate to the originating requirement. This is how a saga cycles backward without re-listing a stage's entry events.

## The entry-event field is `when`, not `on`

A stage's entry events and a loop's trigger both use `when`. **They are deliberately not `on`.** A bare `on:` key in YAML 1.1 is coerced to the boolean `true` (the "Norway problem" family of YAML foot-guns), which would silently destroy the field. The canonical schema uses `when` everywhere for this reason.

The design note's early YAML sketch shows `on:` — it predates this decision. The schema (`packages/schema/schemas/stage-graph.schema.json`) and every authored artifact are the authority: use `when`.

## Referenced-by-id validation

Because `topology`, `compensation`, `gate`, and each `locks` entry are id references, a stage graph is only valid against a workspace that contains those artifacts. The composer (and workspace resolution) `ref`-validate the graph: a stage naming an unknown topology, an undefined gate, a lock naming a [Contract](contract.md) that does not exist, or a `loops` edge to an unknown stage is rejected before publish.

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: StageGraph
metadata:
  id: <lowercase-kebab>       # the pipeline id
  name: <human name>
  description: <what this pipeline sequences>   # min 10 chars
stages:                       # at least one; ids unique
  - id: <stage id>
    topology: <topology id>              # the bounded run this stage kicks
    when: [<event>, ...]                 # entry event(s) — NOT `on:`
    success: <event>                     # emitted on clean completion
    locks: [<contract id>, ...]          # Contract references; all-or-none, fixed order, before the run
    release_locks_on: <event>            # signal that releases the locks
    gate: <funnel id>                    # a Funnel the run parks on
    compensation: <topology id>          # unwind run on cancel-after-pass
loops:                        # optional cross-stage edges (the defect cycle)
  - when: <event>
    to: <stage id>
provenance:
  authored_by: human
  version: 1.0.0
```

Only `apiVersion`, `kind`, `metadata`, `stages`, and `provenance` are required; every field on a stage except `id` and `topology` is optional.

## Minimal example

Two sequential stages driven by events — no locks, gates, or compensation:

```yaml
apiVersion: swarmkit/v1
kind: StageGraph
metadata:
  id: sdlc-pipeline
  name: SDLC Pipeline
  description: Intake a requirement, then kick off design once intake completes.
stages:
  - id: intake
    topology: sdlc-intake
    when: [requirement.created]
    success: design.kickoff
  - id: design
    topology: sdlc-design
    when: [design.kickoff]
provenance:
  authored_by: human
  version: 1.0.0
```

## Full example

Intake → design (contract-locked, gated by a Funnel, compensatable) → build → SIT, with a defect loop. This is the SDLC pipeline the reference controller drives:

```yaml
apiVersion: swarmkit/v1
kind: StageGraph
metadata:
  id: sdlc-pipeline
  name: SDLC Pipeline
  description: >
    A requirement sequenced across intake, design (held on an integration
    contract and gated by a Funnel), build, and SIT as a saga, with a defect
    cycle that routes rework back to triage and re-test.
stages:
  - id: intake
    topology: sdlc-intake
    when: [requirement.created]              # entry event
    success: design.kickoff                  # signal emitted on clean completion
  - id: design
    topology: sdlc-design
    when: [design.kickoff]
    locks: [contract-oms-web, contract-oms-inventory]   # acquired all-or-none before the run
    gate: consolidated-design-approval        # a Funnel gate; the run parks on it
    success: design.approved                  # emitted when the gate approves
    release_locks_on: design.approved         # contract held through approval, then released
    compensation: sdlc-compensate-design      # unwind run if the requirement is cancelled
  - id: build
    topology: sdlc-build
    when: [design.approved]
    success: build.ready-in-qa
  - id: sit
    topology: sdlc-sit
    when: [build.ready-in-qa]                 # EXTERNAL event (CI) — not a SwarmKit signal
    success: sit.passed
loops:                                         # cross-stage edges (the defect cycle)
  - when: defect.raised
    to: defect-triage
  - when: defect.fixed
    to: re-test
provenance:
  authored_by: human
  version: 1.0.0
```

## Authoring a stage graph

The conversational authoring path treats a stage graph like any other artifact: the schema drafter calls `get_schema("stage-graph")` for the exact shape, and `query-swarmkit-docs` surfaces this reference and the design note. The authoring swarm writes the artifact into the workspace `pipelines/` directory via `write_workspace_file`. When authoring, remember: entry events use `when` (never `on`); every `topology`, `gate`, and `compensation` must reference a real workspace artifact; `locks` acquire all-or-none before the run and release on `release_locks_on`; and a stage that passed should declare a `compensation` so the saga always has an unwind path.

## See also

- [Pipeline controller design note](https://github.com/delivstat/swarmkit/blob/main/design/details/pipeline-controller.md) — the authoritative saga model, controller state, event dedup + reconciliation, contract locking, compensation, and the two serve seams (correlation label, gate-resolution notification).
- [Funnel](funnel.md) — the gate a stage's run parks on via `gate:`.
- [SDLC pipeline example design note](https://github.com/delivstat/swarmkit/blob/main/design/details/sdlc-pipeline-example.md) — the five capabilities this pipeline demonstrates.
