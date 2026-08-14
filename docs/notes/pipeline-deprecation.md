# The bundled pipeline orchestrator is deprecated

**Status:** removed in swarmkit-runtime 1.189.0 (deprecated in 1.188.0). Kept as the migration
guide — everything below still describes what replaced it.

## What went

`swarmkit pipeline`, `swarmkit orchestrator`, the pipeline and saga HTTP routes, the `/runs` and
`/pipelines` UI pages, the saga store and reference controller, and the StageGraph schema. A
workspace with a `pipelines/` directory now has those artifacts ignored rather than resolved.

Three things filed beside them were not pipeline-specific and were moved rather than deleted:

* **the inbound event ingress** — `POST /events/signal` (was `/pipelines/signal`), the webhook front
  door, and the MCP `submit_pipeline_event` tool, all now over `triggers/_ingress.py`. Its
  guardrails are why it survived intact: `advance` / `skip` need a reserved human-identity scope no
  agent token can hold, and every attempt is audited whether allowed or denied.
* **`EventSignal`** (was `PipelineSignal`) — a type alias, the seam an application-owned
  orchestrator is driven through. Nothing sets a sink by default now, so `POST /events/signal`
  answers 503 until a deployment assigns one; "nobody is listening" is the honest answer.
* **`swarmkit comprehension`, `cited-change`, `slice-check`** — filed beside `swarmkit gates` but
  about a single run's artifact, now in `cli/_cmd_checks.py`. `gates` itself went: it classified the
  edges of a stage graph, and there are no stages.

`Contract` artifacts still resolve and validate, but `locks` on a stage was their only reader, so
nothing in the runtime consumes one today.

## Why

`design/details/extracting-the-pipeline.md`, in short: SwarmKit runs a swarm over an input and
returns a governed, approved artifact. Deciding what runs next is ordinary sequencing, and the
bundled version grew into a saga engine — durable state, event dedup, lease reclaim, crash
reconciliation, compensation — which is a distributed-systems problem mature engines already solve
and is not one of the three pillars.

## Migrating

`examples/pipeline-orchestrator` is a working sequencer over the public HTTP API, in about 180
lines with no runtime import. Copy it and change the stage list, or port the loop into whatever you
already run — Temporal, Airflow, a cron script.

The loop is five endpoints:

| call | why |
| --- | --- |
| `POST /run/{topology}` | start a stage, with `correlation_id` and `labels` |
| `GET /jobs/{id}` | `status`, and `diff_length` for harness work |
| `GET /review` | find the gate whose `run_id` is this job |
| `GET /gates/{id}` | the verdict, **with quorum and `exclude_author` applied** |
| `POST /jobs/{id}/resume` | continue after approval |

Plus `GET /jobs/{id}/diff` and `GET /artifacts/{ref}` for what a run produced.

**Do not count approved role-tasks yourself.** The policy lives in a funnel your application does
not read; `GET /gates/{id}` applies it.

### What you take on

Retries, backoff, dead-letters, and durability of the *sequence*. The unit of durability in SwarmKit
is the **run** — a run parked on a gate survives a restart by itself — but the order around it is
yours.

### What you keep

Topologies, archetypes, skills, funnels. Running a swarm, validating and judging its output, parking
for human approval, resuming, and the correlated audit and cost trail.

## What will be removed, when it is

Recorded here so the removal is mechanical and reviewable rather than a rediscovery:

- `orchestration/` — saga state, store, reference controller
- `server/_routes_pipelines.py`, `server/_routes_sagas.py`, `server/_pipeline_stage.py`
- `cli/_cmd_pipeline.py`, `cli/_cmd_orchestrator.py`
- UI `/runs` and `/pipelines`, and `lib/gate-coverage.ts`
- `persistence`: `StoreKind.SAGA`, `saga_store()`
- `schemas/stage-graph.schema.json`, last
- `deploy/pipeline`

Two things **stay**, and are not pipeline-specific despite living near it:

- **`PipelineSignal` and `triggers/_pipeline_ingress.py`** — a type alias and a correlated webhook
  front door. That is the inbound integration seam an application-owned orchestrator is driven by.
  It should lose the "pipeline" in its name, not its existence.
- **the funnel-strength half of `gate_coverage`** — "which agents carry a funnel and how strong is
  it" is about topologies and funnels, both of which stay, and belongs beside the reachability
  report. Only the per-stage edge analysis goes with the stage graph.

`pipeline_artifacts` also stays: since 1.179.0 a one-shot run writes to it, so it is the general
artifact store with a historical name.
