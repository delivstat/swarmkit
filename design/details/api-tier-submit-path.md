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
- **`serve --access-audit batched|sync`** (env `SWARMKIT_ACCESS_AUDIT`, or
  `create_app(access_audit=...)`). `batched` is the default and uses the writer above. `sync`
  starts no writer, so `_record_serve_access` writes each row inline before the response, as
  before. This is the opt-out for an operator who needs every acknowledged call durably recorded.
  An unknown value is refused: exit 2 from the CLI, `ValueError` from `create_app`.
- **`_claim_root_logging()`** runs before FastMCP is constructed. It calls
  `logging.basicConfig(level=INFO, format=...)` with a plain `StreamHandler`, so FastMCP's own
  `basicConfig` becomes a no-op. An embedder that already configured logging keeps its own,
  because `basicConfig` adds nothing to a root logger that already has a handler.

## Trade-offs, stated

- **Access audit on `kill -9`.** In `batched` mode, a hard kill loses at most 0.25 s of access
  rows, and they aren't recovered on restart. A clean shutdown loses none, and `sync` mode loses
  none either. The write was already best-effort, since a failing store never broke the request.
  The `serve_access` table is the transport access log, not the run audit trail. Run audit events
  keep their write-through journal (`audit-event-journal.md`), untouched here.
- **Queue bound across processes.** It's approximate by up to one window of other processes'
  admissions, as described above.
- **Acknowledged before durable.** A mutating call's response can reach the client before its
  access row is committed. Before this change, the commit came first. Nothing in the runtime reads
  `serve_access` back on the request path: it's for operators, and the storage CLI only migrates it.
  The order is per process: `created_at` is stamped at record time, and rows from one process are
  inserted in that order. Across processes, `id` order (what `list_access` sorts by) can differ from
  `created_at` order by up to one window.
- **Cancellation.** A thread can't be cancelled, so once a submit is admitted it runs to
  completion under `asyncio.shield`, even if the request is cancelled (for example by a
  graceful-shutdown timeout). Otherwise, a cancelled all-in-one submit would leave its row
  `pending` with nothing to run it, and a queued one would give back a place its row still holds.
  The on-loop insert had no cancellation point, so this keeps the old behaviour. A failure nobody
  is left to observe is logged.
- **Stopping the writer** signals the flush loop and waits for it; it doesn't cancel it. A cancel
  could land while a batch was being written from its thread, which would leave that write's
  failure unseen and let the final flush race it.

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
- **Cancellation**
  - A cancelled all-in-one submit still starts the row it wrote. This failed before the shield.
  - A cancelled queue submit leaves a claimable row, still counted by the bound.
  - `stop()` during a slow flush loses and duplicates nothing, and keeps the order.
- **Access audit**
  - Recording does no I/O, and a flush is one batch call.
  - `created_at` is the record time.
  - A failed flush keeps its rows.
  - The periodic flush writes, and `stop()` writes the rest.
- **The opt-out**
  - In `sync` mode the row is committed before the response, with no writer.
  - `batched` is the default.
  - An unknown mode is refused by `create_app`, and by the CLI from both the flag and the env var.
  - The env var reaches `create_app`.
- **Logging**
  - Serve's root logger is not a `RichHandler`.
  - An embedder's own handler is left alone.

`test_auth.py::test_server_records_access_audit` now reads after shutdown, which flushes. The
existing `test_serve_queue_mode.py` covers the bound and the queued row end to end.

## Demo plan

`examples/loadtest/server_compare.sh api 2000 200`, the Run 7 harness, before and after. The
results are Run 8 in `load-and-scale.md`.
