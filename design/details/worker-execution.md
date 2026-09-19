---
title: Worker execution — decouple run execution from the API loop
description: A durable job queue and worker processes so serve accepts/streams while workers execute, breaking the single-event-loop ceiling the load benchmark found. Postgres SKIP LOCKED by default, a pluggable broker (Redis/arq) optional; the durable record stays in Postgres.
tags: [serve, scaling, persistence, operations]
status: proposed
---

# Worker execution

## The gap

The load benchmark (`load-and-scale.md`, Runs 1–4) found a single `swarmkit serve` process bounded by
the **synchronous per-run work on its one event loop** — the LangGraph checkpointer writes first,
then the job-store commits — so throughput plateaus at a few runs/s and latency grows with
concurrency, and admission (429) is slow while the loop is busy. Compile caching, off-loop audit, and
a bigger pool each helped correctness or a partial term but did not move the knee, because the cost is
the *whole* per-run write path, not one writer, and much of it must stay synchronous within a run
(the job row before the id is returned; the checkpoint before the next node; append-only audit).

You cannot async-away a per-run write that has to be ordered. **The win is parallelism across runs,
not within one:** run each on its own process, so N runs' synchronous writes proceed in parallel. And
today serve executes in-process (`asyncio.create_task`); there is **no durable queue and no atomic
claim** — a job is owned by the process that accepted it, and dies with it.

## Goal

- **serve is the API tier**: accept (enqueue), poll/stream status, resolve gates, read audit — never
  execute. Its loop stays responsive, which also fixes slow admission under load.
- **Workers execute**: a pool of worker processes each claim a queued job atomically, run it (own
  event loop, own DB engine + pool), and record its result — sync within a run, parallel across
  workers. Throughput scales with worker count and cores, past the single-loop ceiling.
- **A crashed worker's job is recovered** by another (lease + heartbeat), with no double execution.
- **No new mandatory dependency**: Postgres, which the durable record already lives in, is the default
  queue. A Redis/broker backend is optional and pluggable for high-fan-out deployments.

## Non-goals

- Not a general task queue for millions of tiny tasks — the unit is a governed agent run (seconds to
  minutes, hundreds concurrent). That shape is why Postgres-as-queue suffices (below).
- Not removing in-process execution — a single serve process keeps running jobs itself (no workers,
  no queue) for `swarmkit serve` out of the box and for SQLite. The worker model is opt-in and
  Postgres-only.
- Not making per-run writes async. They stay synchronous within a run; parallelism comes from
  processes.
- Not the control plane. This is one workspace's execution tier scaling out, not the fleet.

## Design

### The API/worker split

```
              ┌── serve (API) ──┐         ┌── worker ×N ──┐
POST /run ──▶ │ enqueue job     │  queue  │ claim (atomic)│
GET /jobs  ◀─ │ read status     │ ◀─────▶ │ execute run   │
/stream    ◀─ │ stream events   │         │ heartbeat     │
/review    ── │ resolve gates   │         │ write record  │
              └─────────────────┘         └───────────────┘
                        └────── one Postgres ──────┘
```

`serve --role api` runs the FastAPI app with execution disabled; `swarmkit worker` runs the loop that
claims and executes. `swarmkit serve` with no role stays today's all-in-one (accept + execute in
process) for the simple case.

### The queue is an interface; Postgres is the default

```python
class JobQueue(Protocol):
    async def enqueue(self, job_id: str) -> None: ...
    async def claim(self, *, lease_seconds: int) -> str | None: ...   # atomic; None if empty
    async def heartbeat(self, job_id: str) -> None: ...
    async def complete(self, job_id: str) -> None: ...
    async def reclaim_expired(self) -> list[str]: ...                 # leases past deadline
```

**Default — `PostgresJobQueue`:** the `jobs` row *is* the queue entry (`status: queued → claimed →
running → done|failed`), so there is **one durable source of truth** — the job, its audit, its gates
already live there. Claim is the proven pattern:

```sql
UPDATE jobs SET status='claimed', worker_id=:w, lease_until=now()+:lease
WHERE id = (SELECT id FROM jobs WHERE status='queued'
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id;
```

`LISTEN/NOTIFY` on enqueue wakes an idle worker so it is push, not busy-poll. This is what `river`,
`graphile-worker` and `que` do; it is adequate for hundreds of concurrent governed runs, and it adds
**no new infrastructure**.

**Optional — a broker backend** (`arq`/`SAQ`/Redis Streams consumer groups) for deployments that want
very high fan-out or to keep dispatch traffic off Postgres. Even then the **authoritative job/audit/
gate state stays in Postgres**; the broker carries *dispatch only*, and enqueue writes the job row and
the queue entry together so a broker loss cannot lose a governed run's record.

### Crash recovery: lease + heartbeat

A claim sets `lease_until`. The worker heartbeats (extends the lease) while running. A reaper (any
worker, or serve) runs `reclaim_expired` — jobs whose `lease_until` has passed with no completion go
back to `queued`. **Idempotency:** a reclaimed run resumes from its **checkpoint** (the mechanism
`test_kill9_recovery` already proves) rather than restarting, and the write-through audit means the
pre-crash trail is intact — so a reclaim continues, it does not duplicate. A run is bound to
`(job_id, attempt)`; a stale worker finishing after its lease expired detects the version bump and
discards its result.

### Connection budget — the real operational gotcha

Each worker process has its **own** DB engine + pool **and** its own checkpointer connection(s). So

```
physical Postgres connections ≈ N_workers × (store_pool + max_overflow + checkpointer_conns)
```

which multiplies fast — 8 workers × pool 20 = 160, over Postgres's default `max_connections` of 100.
Three levers, all now available or standard:

1. Size the **per-worker pool** down (`SWARMKIT_STORE_POOL_SIZE ≈ max_connections / N_workers`) — the
   pool is per-process configurable as of 1.245.0.
2. Front Postgres with **PgBouncer** (transaction pooling) so many app connections multiplex onto few
   physical ones — the standard answer beyond a handful of workers.
3. Count the checkpointer's separate connection(s), not just the store pool, in the budget.

The claim itself is one light connection per idle worker; `LISTEN/NOTIFY` avoids a poll loop holding
one hot.

### SQLite is single-process only

Multiple worker processes writing one SQLite file contend on its single-writer lock across processes
(the same reason the audit write stays on-loop for SQLite). So the worker model requires Postgres;
`swarmkit serve` on SQLite stays the in-process all-in-one. The runtime refuses `swarmkit worker`
against a SQLite store with a clear message rather than corrupting under contention.

## Test plan

- `PostgresJobQueue`: two workers race to claim one job → exactly one wins (SKIP LOCKED); an expired
  lease is reclaimed; a completed job is never reclaimed. (Gated on `SWARMKIT_TEST_POSTGRES_URL`, like
  the migrate tests.)
- Crash recovery: kill a worker mid-run → another reclaims → the run resumes from its checkpoint and
  completes once; the audit has one run's trail, not two (extends `test_kill9_recovery`).
- API/worker split: `POST /run` enqueues without executing; a worker drains it; `GET /jobs/{id}` and
  `/stream` served by the API tier reflect the worker's progress.
- Throughput: the load harness (`examples/loadtest/`) against `api` + N workers on Postgres shows
  aggregate throughput scaling with N — Run 5, the payoff measurement, with a streaming driver so the
  poller does not mask it.
- SQLite refusal: `swarmkit worker` on a SQLite store exits with the reason.

## Demo / rollout plan

1. **Shipped (1.247.0).** The `JobQueue` interface + `PostgresJobQueue` + `reclaim_expired`, with
   serve still all-in-one by default (no behaviour change).
2. **Shipped (1.248.0).** `swarmkit worker` + `serve --role api`: the API tier enqueues a run as
   `queued` (resolving topology/attachments up front) and never executes; a worker claims it,
   runs it via the exact `execute_job` path, heartbeats, and reclaims abandoned runs. The API tier
   drops its in-memory job stub so `GET /jobs/{id}` reads the durable row a worker keeps current.
   Connection-budget math + PgBouncer documented above.
3. Run 5 in `load-and-scale.md`: api + N workers, aggregate throughput vs N, with the streaming
   driver.
4. Only then consider a Redis backend, if a deployment's fan-out actually needs it.
