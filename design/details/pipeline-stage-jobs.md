# Pipeline stage runs are recorded as jobs, linked to their run

**Status:** implemented (runtime 1.152.0, UI 0.31.0)

## Goal

A pipeline stage executes a topology. Make that execution appear in job history like every other
topology execution, and make it findable *from the pipeline run that caused it*.

## Non-goals

- No new view. `/jobs` and `/runs` already exist; this connects them.
- No change to the saga model. Stage state stays where it is; jobs record the *execution*.
- No backfill. Stages that already ran left no row, and inventing rows for them would be fiction.

## The gap

There were three writers of a topology run and two of them recorded it:

| how a topology runs | job row |
| --- | --- |
| `POST /run/{topology}` (serve's `JobService`) | yes, always |
| `swarmkit run` | yes, since 1.150.0 |
| a pipeline stage (`build_pipeline_run_stage`) | **no** |

So a pipeline showed saga state in `/runs` — stage names, statuses, gate decisions — and nothing at
all in `/jobs`. The work itself was invisible from both: what a stage produced, what it cost, which
trace belonged to it. The most expensive runs in the system were the only ones nobody could look up.

This is the recurring shape: the information existed at the point of execution, nothing wrote it
down, and the absence rendered as an empty table rather than as a gap.

## Design

**The row is keyed by the stage's run id.** `stage_run_id(correlation_id, stage_id)` —
`"WMS-5:design"` — is already the LangGraph thread id and already the trace's `run_id`, so a job row
keyed that way resolves to `.swarmkit/traces/WMS-5:design.json` and to
`/observability/runs/WMS-5:design/trace` with no mapping table. A serve-started job cannot do this;
it mints a separate id.

**The row also carries `correlation_id`.** The id is human-readable and greppable, but selecting one
run's stages by parsing ids apart is the kind of thing that works until a stage id contains a colon.
A column is filterable, indexable, and honest about the relationship:

```sql
ALTER TABLE jobs ADD COLUMN correlation_id TEXT;   -- null for a standalone run
```

Additive, and applied by `_migrate_jobs()` on open — `create_all` creates tables, it does not alter
them, so an existing workspace needs the explicit `ALTER`.

**The store is injected, not resolved.** `build_pipeline_run_stage(..., job_store=...)` takes the
store serve already built through `storage_for_workspace` (`design/details/storage-service.md`). A
stage that opened its own would ignore the workspace's storage config and could write jobs to a
different backend from the one the UI lists — the exact class of bug the storage service exists to
prevent. A test asserts `storage_for_workspace` does not appear in the stage module.

**Recording is one-directional, as everywhere else.** A store that will not open, or fails
mid-write, loses the *record* of a stage — never the stage. Both helpers swallow and log.

**Every exit path closes the row.** Completed, raised, unknown topology, and the harness shape (a
node that failed *without* raising, carried in `result.node_errors`). A row left at `running` is the
stalled-saga shape: indistinguishable from work still in flight.

## Surface

- `Store.create_job(job_id, topology, user_input, correlation_id=None)`
- `Store.list_jobs(limit=100, correlation_id=None)`
- `GET /jobs/history?correlation_id=WMS-5` — one run's stages, newest first
- `/jobs` gains a **Pipeline** column linking to `/runs?run=<correlation_id>`

## Test plan

`test_pipeline_stage_records_a_job.py` — the row exists, is linked, is keyed by the run id, exists
*before* the run, records output and usage, and closes on all four exit paths; plus the two
one-directional cases and the no-own-store assertion. `test_persistence.py` covers the column and
the filter, including that a standalone run has none.

## Demo

`packages/runtime/demos/pipeline_stage_jobs.py` — runs a two-stage pipeline against a stub runtime
and prints the resulting job rows and the filtered query.
