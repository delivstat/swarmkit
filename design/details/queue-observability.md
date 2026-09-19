---
title: Queue observability — claim/start timestamps and queue-stats
description: Make the durable job queue observable as a production subsystem — record when a run was claimed and when execution began, and expose depth, oldest-unclaimed age, and queue-wait / execution-latency percentiles. Follow-up to worker-execution.md from the scaling review.
tags: [serve, scaling, persistence, observability, operations]
status: proposed
---

# Queue observability

## The gap

`worker-execution.md` made execution scale with worker count, and `load-and-scale.md` Run 5 measured
it. But an external review noted the queue is not yet observable *as a queue*: end-to-end latency was
only decomposable after the fact, and there is no first-class signal for backlog depth, the age of
the oldest waiting run, or how long runs actually wait before a worker claims them. Queue depth alone
is not enough — a thousand 500 ms jobs is a different situation from a thousand ten-minute ones.

## Goal

- Record two timestamps the runtime did not keep: **`claimed_at`** (when a worker claimed the run)
  and **`started_at`** (when execution actually began). With `created_at` and `completed_at` already
  stored, the four decompose the lifecycle:
  - queue wait = `claimed_at − created_at` (queue mode) or `started_at − created_at` (all-in-one)
  - execution latency = `completed_at − started_at`
  - end-to-end = `completed_at − created_at`
- Expose **`GET /queue/stats`**: queued/running depth, oldest-unclaimed age, queue-wait and
  execution-latency p50/p95 over recent completions, and depth by topology.
- Surface it in the CLI (`swarmkit queue-stats`) now; the portal's Jobs-view stat strip lands with
  the batched portal UI slice (so the browser/screenshot pipeline is stood up once, not per feature).

## Non-goals

- Not a metrics/Prometheus exporter — this is a point-in-time snapshot endpoint; a scrape exporter is
  a separate concern that can read the same store.
- Not queue *policy* (max-depth admission shipped in 1.250.0; per-tenant quotas and priority are
  their own follow-up — `fairness`).
- Not changing the claim mechanism.

## Design

### Two additive columns

`claimed_at` and `started_at` (both `TEXT`, ISO-8601, nullable) join the additive `jobs` migration
alongside `worker_id`/`lease_until`/`attempt`. `PostgresJobQueue.claim` stamps `claimed_at` in the
same atomic UPDATE that marks the row `running`; `execute_job` stamps `started_at` on its first
durable write (both modes, so an all-in-one run has `started_at` but no `claimed_at`). Old rows read
NULL and are simply excluded from percentile windows.

### `GET /queue/stats`

Engine-agnostic (no `percentile_cont`, which SQLite lacks): the store reads the recent completed rows
(bounded, most-recent-N) and computes percentiles in Python, the way the load driver does. Returns:

```json
{
  "queued": 42, "running": 8,
  "oldest_queued_age_seconds": 12.4,
  "queue_wait_p50_seconds": 1.1, "queue_wait_p95_seconds": 6.3,
  "execution_p50_seconds": 0.64, "execution_p95_seconds": 0.9,
  "depth_by_topology": {"typical": 30, "mcp-heavy": 12},
  "sample_size": 500
}
```

Read-only, `serve:read`. Cheap: two counts, one min, one bounded recent-rows scan.

## Test plan

- `Store.queue_stats` on a seeded mix (queued/running/completed with known timestamps) returns the
  expected depth, oldest age, depth-by-topology, and percentile shape.
- `claim` stamps `claimed_at`; a run stamps `started_at`; the two survive the additive migration
  (extends the migrate-sequence test).
- `GET /queue/stats` returns the snapshot and requires `serve:read`.

## Demo plan

- CLI transcript: `swarmkit queue-stats` against a workspace with a seeded backlog (shipped here).
- Portal: a Jobs-view stat strip (queued depth / oldest age / p95 wait) with a screenshot — delivered
  in the batched portal UI slice, which stands up the playwright/chromium screenshot pipeline once.
