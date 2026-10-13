---
title: API-tier submit path — one insert, a cached queue bound, batched access audit
description: POST /run on serve --role api made ~23 synchronous database round trips on the event loop and logged through rich; now one off-loop insert, a queue-depth gauge read at most every 250 ms, access-audit rows written in batches, and a plain log handler (issue 1084).
tags: [serve, scaling, queue, persistence, audit, logging, performance]
status: implemented
---

# API-tier submit path — one insert, a cached queue bound, batched access audit

## Goal

`serve --role api` (`worker-execution.md`) exists so the API tier is cheap: accept, enqueue,
return. One process accepted only ~28 submits/s, with p99 near 30 s under a 200-connection storm,
because each `POST /run` did synchronous database work on the event loop (issue 1084;
`load-and-scale.md` Run 7). The goal is to take that work off the request path without weakening
the queue bound or losing an access-audit row. Measured in `load-and-scale.md`, Run 8.

## What a submit cost

Profiled in-process (200 sequential submits, Postgres on the same host): 60 ms wall, 40 ms CPU.

| call | site | what it was |
|---|---|---|
| `count_jobs("queued")` | `_services.py`, queue-mode admission | a `COUNT(*)` per request, the 429 path included |
| `create_job` | `_store.py` | the insert |
| `update_job` ×1–2 | same submit | set `version`, then `queued` + class + priority on the row just inserted |
| `record_access` | auth middleware | one transaction per mutating request |
| request log line | `log_requests` | rendered through `rich` — see below |

On Postgres each call is a pool checkout, a pre-ping, and BEGIN/statement/COMMIT, so about 23
round trips per submit, all on the loop.

The `rich` cost was not serve's. FastMCP's constructor calls
`logging.basicConfig(handlers=[RichHandler(...)])`. Under `swarmkit serve` nothing had configured
the root logger yet, so it took, and every log line in the process went through rich's table layout.

## API shape

No HTTP or CLI change.

- **`Store.create_job(..., *, status, version, job_class, priority)`.** The submit path writes the
  row once, born `queued` in queue mode. Unset columns keep their table defaults; existing callers
  are unchanged.
- **The insert runs in a worker thread** (`asyncio.to_thread`), like the Postgres audit write
  already does. A failed insert drops the in-memory stub, so it is not left behind as a phantom
  `pending` job.
- **`_QueueDepthGauge`, one per store.** Re-reads `COUNT(*)` at most every
  `QUEUE_DEPTH_MAX_AGE_S` (0.25 s). In between, it adds this process's own work, kept apart from
  the reading:
  - `landed`: inserts since the read;
  - `in_flight`: places reserved at admission whose insert hasn't finished.

  They are kept apart because a re-read cannot see an uncommitted insert. The first version folded
  reservations into the reading, and a 1,000 bound admitted 1,001 under the storm. Within one
  process the gauge can now only over-count. Across API processes it can overshoot by what the
  others admit in one window. The bound exists to stop an unbounded backlog, not to be exact.
- **`AccessAuditWriter`** (`server/_access_audit.py`):
  - `record()` appends to an in-memory buffer and does no I/O.
  - A background task writes each batch in one transaction (`Store.record_access_many`) from a
    worker thread every 0.25 s. The lifespan starts it and flushes on shutdown.
  - Rows are stamped when recorded, not when written.
  - A failed flush keeps the rows for the next one. The buffer is capped at 10,000, and drops past
    the cap are logged with a count.
  - An app without the lifespan, such as an embedder calling the helper directly, still writes
    inline.
- **`_claim_root_logging()`** runs before FastMCP is constructed. It calls
  `logging.basicConfig(level=INFO, format=...)` with a plain `StreamHandler`, so FastMCP's own
  `basicConfig` becomes a no-op. An embedder that already configured logging keeps its own,
  because `basicConfig` adds nothing to a root logger that already has a handler.

## Trade-offs, stated

- **Access audit on `kill -9`.** A hard kill loses at most 0.25 s of access rows; a clean shutdown
  loses none. The write was already best-effort, since a failing store never broke the request.
  The `serve_access` table is the transport access log, not the run audit trail. Run audit events
  keep their write-through journal (`audit-event-journal.md`), untouched here.
- **Queue bound across processes.** It's approximate by up to one window of other processes'
  admissions, as described above.

## Non-goals

- **`pool_pre_ping`.** It costs a round trip per checkout, but it guards against stale connections
  after a Postgres restart. Dropping it is a resilience decision, not part of this fix.
- **An async database driver for the store.** That would be a whole-store change. Thread offload
  covers the one write left on the submit path.
- **The HTTP server.** Run 7 and Run 8 both measure Granian slower than uvicorn here (#1085).

## Test plan

`packages/runtime/tests/test_api_tier_submit_path.py`:

- **One insert per submit**
  - `create_job` writes `status`/`version`/`job_class`/`priority` in the insert.
  - The defaults are unchanged.
  - A queue-mode submit through HTTP calls `create_job` once and `update_job` never.
- **Queue bound**
  - The backlog is read at most once per window.
  - A reserved place is visible before the next read.
  - A re-read still counts inserts in flight (the 1,001 regression).
  - 20 concurrent submits against a bound of 5 admit exactly 5.
  - A failed insert gives its place back.
- **Access audit**
  - Recording does no I/O, and a flush is one batch call.
  - `created_at` is the record time.
  - A failed flush keeps its rows.
  - The periodic flush writes, and `stop()` writes the rest.
- **Logging**
  - Serve's root logger is not a `RichHandler`.
  - An embedder's own handler is left alone.

`test_auth.py::test_server_records_access_audit` now reads after shutdown, which flushes. The
existing `test_serve_queue_mode.py` covers the bound and the queued row end to end.

## Demo plan

`examples/loadtest/server_compare.sh api 2000 200`, the Run 7 harness, before and after. The
results are Run 8 in `load-and-scale.md`.
