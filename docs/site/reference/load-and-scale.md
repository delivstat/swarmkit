# Load and scale

What `swarmkit serve` does under concurrent load, measured — so you can size a deployment instead of
guessing. The harness that produces these numbers is [`examples/loadtest/`](https://github.com/delivstat/swarmkit/tree/main/examples/loadtest);
you can reproduce and extend them on your own hardware.

## Measurement runs

These numbers are a **baseline**, dated so a post-improvement run can be compared against it.

| Run | Date | Runtime | Box | Notes |
|---|---|---|---|---|
| 1 (baseline) | 2026-09-18 | 1.242.0 | 1 dev machine, WSL2 (shared) | first harness run; synchronous per-run work on the event loop |
| 2 | 2026-09-18 | 1.243.0 | same box | compile now cached — isolates the DB-on-loop term |
| 3 | 2026-09-18 | 1.244.0 | same box | audit write off-loop + pool=100 — one of several sync writes moved |
| 4 | 2026-09-19 | 1.245.0 | same box | profiled a run + isolated driver vs serve — located the cost, corrected earlier runs |
| 5 | 2026-09-19 | 1.249.0 | same box | the API/worker split under load — throughput scales with worker count (the payoff) |

Run 5 is the payoff measurement: the fix Runs 1–4 pointed to — decoupling execution into worker
processes (`design/details/worker-execution.md`) — shipped in 1.247.0–1.249.0, and this run shows
aggregate throughput now scales with the number of workers, past the single-event-loop ceiling.

## How serve runs work (the model these numbers reflect)

- `POST /run/{topology}` is **async**: it creates a job, spawns an `asyncio` task, and returns a job
  id immediately. You poll `GET /jobs/{id}` (or stream it).
- One `swarmkit serve` process is **one uvicorn worker = one event loop**. Runs are asyncio tasks on
  that loop, bounded by a semaphore — `server.jobs.max_concurrent` (default 5).
- **Admission depends on the mode**, and the two modes differ deliberately:

  | | all-in-one (`swarmkit serve`) | API tier (`swarmkit serve --role api`) |
  |---|---|---|
  | Who executes | this process (asyncio tasks) | separate `swarmkit worker` processes |
  | `server.jobs.max_concurrent` | bounds active runs on this loop | does not apply here — it governs each worker |
  | Backlog | none — nothing is queued beyond the running set | a durable Postgres queue of `queued` jobs |
  | When you get **429** | all `max_concurrent` slots are taken (reject-when-full) | the queue is full — `--max-queue-depth` runs already `queued` (default 10,000; 0 = unbounded) |
  | Under sustained overload | callers are rejected immediately | the queue grows to the bound, then rejects; a bounded backlog, not unbounded acceptance |

  The bound on the API tier matters: without it, `--role api` would *accept* work that no worker may
  reach for an unbounded time. It is a queue-depth limit, not a per-run concurrency limit.
- A run's model calls are I/O (they `await`), so they overlap freely. But the **CPU- and DB-bound
  sections of a run — topology compile, governance, and the write-through audit persist — are
  synchronous and run on that one event loop.** This is the fact the numbers below turn on.

## Method

Measured with the mock provider and a synthetic per-call latency
(`SWARMKIT_MOCK_LATENCY_MS=2000`, ±25% jitter) so runs take a realistic ~2s without a real model —
the benchmark measures the *runtime*, not the provider. The `ramp` driver holds N concurrent runs
(submit → poll to completion) for 20s per level and records throughput, run-latency percentiles, the
429 rate, and the serve process's RSS/fds from `/proc`.

Numbers below are from one developer machine (WSL2, shared box) and are about **shape, not absolute
peak** — the ratios and the knee are what transfer; your hardware sets the constants. `max_concurrent`
was 1000 for the ramps (so nothing is rejected — we are measuring execution, not admission).

## Run 1 (baseline, 2026-09-18, runtime 1.242.0)

### Result 1 — throughput plateaus early; latency grows linearly past the knee

`tiny` topology (1 agent, 1 model call), Postgres backend, 2s simulated latency:

| concurrency | throughput (runs/s) | run p50 | run p95 | run p99 | 429 |
|---|---|---|---|---|---|
| 5   | 2.25 | 2.4s  | 2.8s  | 3.2s  | 0 |
| 25  | 3.3  | 9.0s  | 12.0s | 12.3s | 0 |
| 50  | 2.55 | 35.2s | 36.5s | 36.5s | 0 |
| 100 | 5.0  | 69.6s | 73.5s | 73.6s | 0 |

Read that carefully: **throughput does not rise with concurrency** (it sits around 2.5–5 runs/s),
and **run latency grows roughly linearly** with offered concurrency (2.4s → 70s). With 2s model calls
that overlap freely, ideal throughput at c=50 would be ~25 runs/s; we see ~2.5. So effective
concurrency is ~5–6 regardless of the semaphore — the synchronous per-run sections serialize on the
single event loop, and extra concurrency just queues behind them.

**The knee is early — around c=5–10 for a process doing this much per-run work.** That is the number
to operate by, not the maximum.

### Result 2 — the backend matters, but is not the ceiling

Same `tiny` ramp on SQLite vs Postgres:

| concurrency | SQLite runs/s (p50) | Postgres runs/s (p50) |
|---|---|---|
| 5  | 2.35 (2.3s) | 2.25 (2.4s) |
| 25 | 4.6 (6.1s)  | 3.3 (9.0s) |
| 50 | 2.55 (26s)  | 2.55 (35s) |

SQLite (local file, no network round-trip) is marginally faster mid-ramp, but **both plateau and both
collapse at c=50**. The DB is a contributor — the write-through audit journal makes several
synchronous `INSERT`s per run — but it is not the whole story; the ceiling is the aggregate
synchronous work per run on the loop.

### Result 3 — fan-out costs about the same as tiny

`typical` topology (leader → reviewer/security/tester, 4 model calls, children in parallel),
Postgres, 2s latency:

| concurrency | throughput | run p50 | run p95 |
|---|---|---|---|
| 5  | 2.1  | 2.5s  | 3.5s  |
| 25 | 2.75 | 9.8s  | 14.9s |

The four model calls overlap (p50 at c=5 is ~one call, not four), so a realistic fan-out topology is
bounded by the same per-run overhead as the tiny one, not by the number of agents. The agent count is
not what limits a single process; the per-run synchronous work is.

### Result 4 — admission is correct, but slow while the loop is busy

`storm`: 10,000 `POST /run` at `max_concurrent=5`, 2s runs:

```
accepted = 500     429 = 9,459     errors = 41
submit p50 = 5.2s   submit p99 = 14.5s   ~36 submit/s
```

The **behaviour is correct** — serve rejects the excess with 429, never crashes, never queues beyond
the cap. But the **rejections are slow** (p50 5.2s): a 429 is cheap, yet it waits behind the accepted
runs' synchronous sections on the same event loop. So "reject fast and clean" holds for *clean*, not
yet for *fast* under sustained execution load. A client should treat 429 as backpressure and back
off; do not rely on instant rejection while the instance is saturated.

### Result 5 — memory and fds are not the constraint (for model runs)

Base RSS ≈ 206 MB (the process + all extras loaded). Across the whole ramp to c=100 it grew to
≈ 224 MB — on the order of **~0.2 MB per concurrent run** — and open fds were stable (~54). For
model-only runs, memory is not the limiting resource here; CPU-on-the-loop is. (Harness runs are
different: each holds a git worktree and a subprocess, so budget memory and fds per *harness* run
separately — measure the `mcp-heavy` and a harness shape for your own mix.)

## Run 2 (2026-09-18, runtime 1.243.0) — compile caching, and what it revealed

The first optimization landed: the LangGraph graph is now **compiled once per topology and reused**
across runs (it was rebuilt every run — pure CPU on the loop). Same `tiny` ramp, Postgres, 2s
latency:

| concurrency | Run 1 runs/s (p50) | Run 2 runs/s (p50) |
|---|---|---|
| 5   | 2.25 (2.4s) | 2.2 (2.4s) |
| 25  | 3.3 (9.0s)  | 3.55 (8.2s) |
| 50  | 2.55 (35s)  | 2.55 (38s) |
| 100 | 5.0 (70s)   | 5.0 (68s) |

**The knee did not move.** Compile caching is a real, correct change — it removes redundant per-run
CPU and is a prerequisite for using more cores — but it barely touched the plateau. That is the
finding: **compile is not the dominant term; the synchronous DB writes are.** Each run does several
blocking `INSERT`s on the event loop (the write-through audit journal is now per-event), and the
store engine uses SQLAlchemy's default connection pool (size 5). A small sync pool + per-event
blocking writes caps effective concurrency at ~5 regardless of `max_concurrent` — which is exactly
what both runs show.

So the next change targets that: **move the store/audit writes off the loop** (an async driver or
`asyncio.to_thread`), coalesce the journal's per-event flush, and size the pool for the target
concurrency. Run 3 tried a first slice of this — read on; the lesson there is that a *partial* move
does not move the knee.

## Run 3 (2026-09-18, runtime 1.244.0) — off-loop audit write + configurable pool

Two more changes: the write-through audit `INSERT` now runs off the event loop
(`asyncio.to_thread`), and the store connection pool is configurable
(`SWARMKIT_STORE_POOL_SIZE`, default 20) — set to 100 for this run. Same `tiny` ramp:

| concurrency | Run 2 runs/s (p50) | Run 3 runs/s (p50) |
|---|---|---|
| 5   | 2.2 (2.4s)  | 2.05 (2.6s) |
| 25  | 3.55 (8.2s) | 3.25 (9.0s) |
| 50  | 2.55 (38s)  | 2.55 (39s) |
| 100 | 5.0 (68s)   | 5.0 (77s) |

**Still no movement — and that is informative.** Moving *only* the audit write off the loop was not
enough, because a run does several other **synchronous** store writes on the loop that are still
there: `create_job`, `update_job` (running, then completed), the per-run usage write, and the trace
file. Audit is one of ~six; moving one leaves the ceiling where it was. The DB-on-loop hypothesis is
not disproven — it is under-tested until *all* the per-run store writes are off the loop (or on an
async driver). A larger pool likewise cannot help while the writes themselves still block the single
loop; it is necessary for scale-out (and now tunable), not sufficient on its own.

So Run 4 is the real test: move the whole per-run store write path off the loop (job store + usage +
trace, not just audit) or switch to an async DB driver, then re-measure. **A note on the rig:** the
driver, serve, and Postgres share one 8-core box, so absolute peaks are contended; the *shape*
(flat throughput, linear latency, unmoved by three partial fixes) is what these runs establish, and a
clean number needs the driver on a separate host.

## Run 4 (2026-09-19) — profiling, and a correction to Runs 1–3

Rather than move more writes off the loop blind, this run profiled a single run and isolated the
driver from serve. Two experiments:

**A. In-process, one run at a time (no HTTP, no poll, no model latency).** 100 sequential
`WorkspaceRuntime.run` calls: **62 ms/run, ~16 runs/s**. The `cProfile` self-time is *distributed*,
not one hotspot — the biggest terms are the checkpointer's async I/O wait (`epoll`, ~27%), the
**four SQLite `commit`s per run** (~15%: create_job, update_job×2, and the usage/trace writes),
SQLAlchemy statement construction that is rebuilt each run (~8%), and even **five `mkdir`s per run**
(~1%). No single fix is 80% of it — it is death by a thousand synchronous cuts on the loop.

**B. Over HTTP at zero model latency (isolating the driver).** The polling driver at c=5 gets
~10 runs/s (vs 16 in-process — the poll adds overhead), and then throughput *degrades* with
concurrency: SQLite 10 → 8 → 4 runs/s (c=5/25/50), Postgres worse at low c (per-write network
round-trips, still serialized on the loop).

**The correction:** Runs 1–3 attributed the plateau mostly to "the single event loop serializing
runs." That is real, but two things were conflated. (1) The **polling driver** (100 ms poll,
submit-then-poll per virtual user on one loop) is itself a meaningful limiter — some of the earlier
ceiling was the measurement, not serve. (2) Within serve, the cost that serializes under concurrency
is the **whole per-run synchronous write path — checkpointer-dominant, then the job-store commits —
not the audit write** that Run 3 moved. Moving one writer off the loop could not help while the
checkpointer and job store still write synchronously on it.

So the real levers, now evidenced:

- **Move the entire per-run write path off the loop / async** — the LangGraph checkpointer first (it
  is the largest term), then the job-store + usage + trace writes. Piecemeal does not move the knee;
  the checkpointer is the one to start with, not audit.
- **Or decouple execution into worker processes** (a durable job queue with an atomic claim; see
  `design/details/worker-execution.md`) — the API loop stops doing run work at all, which also fixes
  admission latency under load. *(This shipped after Run 4 — see Run 5 for the measurement.)*
- **Measure with a streaming (SSE) or multi-process driver**, not a poller, so serve's ceiling is not
  masked by the driver's.

Both runtime levers are substantial (the checkpointer is an async-saver seam; worker execution is an
architecture change), so they are deliberately not rushed here — Run 4's value is locating the cost
correctly so the next change targets the checkpointer/write-path, not another single writer.

## Run 5 (2026-09-19, runtime 1.249.0) — the API/worker split scales with worker count

Runs 1–4 located the ceiling: a single `swarmkit serve` process serializes the per-run synchronous
write path on its one event loop. The fix (`design/details/worker-execution.md`) shipped —
`swarmkit serve --role api` accepts and enqueues, and N `swarmkit worker` processes each claim a
queued job and execute it on their own loop + DB engine, sharing one Postgres. Run 5 measures
whether aggregate throughput now scales with N.

Two independent measurements, on the `typical` topology (leader + three reviewers, fan-out off) at
500 ms mock latency per model call, one shared Postgres:

**A. End-to-end through the API tier**, streaming (`run5.sh` → `serve --role api` + N workers, the
SSE driver so completion detection does not poll):

| workers | throughput (runs/s) | vs 1 worker | run p50 | run p95 |
|---|---|---|---|---|
| 1 | 1.43 | 1.00× | 32.4 s | 33.9 s |
| 2 | 2.67 | 1.87× | 16.3 s | 19.8 s |
| 4 | 4.67 | 3.27× | 8.4 s | 16.2 s |
| 8 | 8.43 | 5.90× | 4.4 s | 12.9 s |

**B. Isolated worker drain** (`run5_drain.py` seeds a fixed backlog straight into Postgres and times
N workers draining it — no API tier in the measurement, so it isolates execution throughput):

| workers | throughput (runs/s) | per-worker | vs 1 worker |
|---|---|---|---|
| 1 | 1.48 | 1.48 | 1.00× |
| 2 | 2.81 | 1.40 | 1.90× |
| 4 | 4.83 | 1.21 | 3.26× |
| 8 | 7.75 | 0.97 | 5.24× |

The two agree within ~5–9%, and both say the same thing: **throughput scales with worker count** —
5.9× (end-to-end) / 5.2× (isolated) going 1→8 workers, monotonic, with no plateau in this range.
Compare Runs 1–4, where a single process flattened at ~2.5–5 runs/s past its knee. The
single-event-loop ceiling is lifted; you buy throughput by adding worker processes.

Two honest caveats the numbers show:

- **Scaling is sublinear** — per-worker throughput falls from 1.48 to 0.97 runs/s (a ~35% drop) as
  N goes 1→8. The workers share one Postgres (the checkpointer and job-store writes contend) and one
  box's cores (this is a shared WSL2 dev machine). A bigger Postgres (or PgBouncer, per the
  connection-budget note), more cores, and per-worker pools sized to fit are what recover it; the
  shape is expected, not a defect.
- **Latency here is queue wait, not execution.** Offered load (50 concurrent) far exceeds capacity
  at low N, so the queue backs up and p50 is dominated by time-in-queue — which is exactly why it
  *halves each time workers double* (32→16→8→4.4 s). A single run's execution is ~640 ms; the rest
  is backlog. Size the worker pool to the offered load to keep the queue shallow.

A measurement note worth keeping: the ramp driver first reported ~2× these throughputs because it
divided completions by the intended window (30 s) rather than the actual wall time. Under a deep
backlog, each virtual user's last run finishes tens of seconds after the window closes, so the real
span is longer and the naive rate is inflated. The driver now divides by measured elapsed; a warm
single-worker cross-check (641 ms/run from Postgres `completed_at` timestamps) confirms the
corrected figure. Short-run measurements (Runs 1–4) were close either way, but under queueing the
distinction is 2×.

## What this means for sizing

- **A single serve process is not a throughput engine for CPU-bound-per-run work.** Plan for
  effective concurrency of ~5–10 per process at this per-run cost. Set `server.jobs.max_concurrent`
  to *bound latency* (keep it near the knee), not to chase throughput — a higher cap past the knee
  buys linear latency growth, not more runs/s.
- **Scale throughput with workers (the primary lever, Run 5).** Run `swarmkit serve --role api` for
  the front door and N `swarmkit worker` processes for execution, sharing one Postgres (the claim is
  atomic — `design/details/worker-execution.md`). Aggregate throughput scales with N (measured 5.9×
  at 8 workers). On one multi-core box, N ≈ cores is the first thing to try; beyond a handful of
  workers, size `SWARMKIT_STORE_POOL_SIZE` down and/or front Postgres with PgBouncer so
  `workers × (pool + overflow + checkpointer)` stays under `max_connections`. (Running N all-in-one
  serve processes behind a load balancer also works and predates the split; the API/worker split is
  the cleaner shape — the API tier stays responsive because it never executes.)
- **Pool sizing is a Postgres concept, not a SQLite one — deliberately.** Postgres is a client/server
  database: N pooled connections are N real parallel sessions, and `SWARMKIT_STORE_POOL_SIZE` sizes
  that (keep pool + overflow, times instances, under Postgres `max_connections`). SQLite is an
  embedded file with a **single writer** — writes serialize on a file lock no matter how many
  "connections" you open, so a pool would buy no write concurrency and can worsen `database is
  locked` contention. Its real levers are WAL mode + `busy_timeout`, which the engine already sets.
  So the knob applies to Postgres only; that is not an inconsistency, it is the two engines being
  honestly different.
- **Treat 429 as backpressure** in the client (retry with jitter), because admission latency
  degrades under load on a saturated instance.

## The honest optimization target

Compile caching (Run 2) and the audit write + configurable pool (Run 3) are done. Runs 1–4 pointed
at two levers: move the per-run write path off the loop, **or** decouple execution into worker
processes. The second shipped (worker-execution.md, 1.247.0–1.249.0) and **Run 5 confirms it lifts
the ceiling** — throughput now scales with worker count. That is the primary answer: parallelism
comes from processes, and the per-run writes stay synchronous *within* a run where they must be
ordered (the job row before the id returns, the checkpoint before the next node, append-only audit).

The first lever is still worth doing and now stacks on top: within a single process — the all-in-one
server, or one worker — the per-run write path (checkpointer-dominant, then the job-store commits)
is still synchronous on that process's loop, which is why per-worker throughput is ~1.5 runs/s and
falls under contention. Moving it off-loop (async saver, `asyncio.to_thread`, a coalesced journal
flush, a pool sized for the target) would raise the per-worker figure and so the whole curve. This
benchmark remains the evidence and the way to measure that change.

## Reproduce

```bash
examples/loadtest/run.sh tiny    tiny    2000 "5,10,25,50,100" 20      # the ramp above
examples/loadtest/run.sh typical typical 2000 "5,25,100"       20 1    # fan-out
export SWARMKIT_STORE_URL=postgresql://user:pw@host:5432/db            # realistic write load
uv run python examples/loadtest/driver/loadtest.py storm --topology tiny --n 10000   # admission

# Run 5 — the API/worker split (Postgres required):
SWARMKIT_STORE_URL=postgresql://... examples/loadtest/run5.sh typical typical 500 50 30 "1,2,4,8"
SWARMKIT_STORE_URL=postgresql://... uv run python examples/loadtest/run5_drain.py \
    --workspace examples/loadtest/workspaces/typical --backlog 200 --workers 1,2,4,8 --latency-ms 500
```

## Not yet measured (run these on your hardware)

- **Long soak (1h / 6h / 24h)** — memory growth, fds, DB connections, task cleanup. The harness
  samples RSS/fds already; run a low, steady concurrency for hours and watch the trend. Bursts pass
  where soaks fail.
- **A real provider once** — not for throughput, for failure behaviour (rate limits, retries,
  timeouts) the mock cannot produce.
- **Scale-out past 8 workers, and >1000 concurrency** — Run 5 measured N≤8 workers on one box and
  found ~5.9× at 8; push N further (more cores, a bigger Postgres or PgBouncer) to find where
  per-worker throughput and the connection budget flatten it, and measure the 429/admission curve on
  the API tier under >1000 concurrent clients.
- **kill -9 during a run** — automated as `packages/runtime/tests/test_kill9_recovery.py`: the run
  resumes from its checkpoint and, since the audit is a write-through journal, the pre-kill trail
  survives (`design/details/audit-event-journal.md`).
