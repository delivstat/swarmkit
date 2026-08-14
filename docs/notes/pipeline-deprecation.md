# The bundled pipeline orchestrator is deprecated

**Status:** deprecated in swarmkit-runtime 1.188.0. Still supported; removal is a later release.

## What this means today

Nothing breaks. `swarmkit pipeline`, `swarmkit orchestrator`, the pipeline and saga HTTP routes and
the `/runs` and `/pipelines` UI pages all keep working and keep getting bug fixes.

What stops is **growth**. No event routing, no fan-out, no cycles, no new stage-graph fields. A
workspace that needs those is a workspace that should own its sequencing, and that signal is more
useful than the feature would be.

Running it now prints one deprecation line per process — once, not per command.

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
