---
title: Per-run write path — build the store once, write each audit event once
description: Two avoidable on-loop costs per run — the runtime store rebuilt (schema checks + a new SQLite engine) on every call, and every audit event written twice — removed; +46% runs/s per process at saturation on SQLite (issue 1080).
tags: [serve, scaling, persistence, audit, performance]
status: implemented
---

# Per-run write path — build the store once, write each audit event once

## Goal

Take the two largest avoidable costs out of a run's synchronous, on-loop work, so one process
completes more runs per second and admission latency under load drops. Measured in
`docs/site/reference/load-and-scale.md`, Run 6.

## The two costs

Profiled over 200 sequential `tiny` runs (mock provider, 0 ms latency, SQLite):

1. **`StorageService.store()` rebuilt the store on every call** (~6.6 ms/run). `Store.__init__`
   runs `create_all_idempotent` and two column-inspection migrations; `SqliteStore` opened a new
   engine each time. The run path calls it once per run for the stop checker.
2. **Every audit event was written twice** (~half of ~18.5 ms/run). The write-through journal
   (`audit-event-journal.md`) persists each event when it is recorded; the end-of-run batch then
   re-inserted the same events and relied on primary-key dedup to reject them. Dedup made that
   harmless, not free — each rejected insert is a write transaction on the loop.

## API shape

No public API change.

- `StorageService.store()` memoizes its `Store` per service, like `engine()` already does. A
  `Store` holds only its engine and is thread-safe through the engine's pool.
  `reset_storage_cache()` still drops it with the service.
- `WorkspaceRuntime._journal_write` records each event id it **successfully** wrote, keyed by run.
  `_persist_events_to_audit` pops that run's set and skips those events. A journal write that
  raised is not in the set, so the batch still writes it — the safety net is unchanged.

## Non-goals

- **`PRAGMA synchronous=NORMAL`.** It would cut each remaining SQLite commit further, but
  `audit-event-journal.md` promises the trail survives power loss. That is a durability decision
  for an operator, not a perf fix to slip in.
- **Checkpointer lock contention** — measured and deferred in #1081.
- **Swapping the event loop or HTTP server.** uvicorn already runs uvloop (libuv); the cost is
  Python work on the loop, which neither touches.

## Test plan

- `test_storage_service.py::test_the_store_is_built_once_per_service` — `create_all` runs once
  across repeated `store()` calls.
- `test_audit_journal.py`:
  - `test_the_batch_does_not_rewrite_an_event_the_journal_wrote` — one `record` call, not two.
  - `test_the_batch_still_writes_an_event_whose_journal_write_failed` — the retry still happens.
  - `test_the_journaled_ledger_is_dropped_when_the_run_ends` — no per-run growth in a long-lived
    serve.
- The existing `test_the_end_of_run_batch_does_not_duplicate_a_journaled_event` and the kill -9
  recovery test keep covering correctness.

## Demo plan

`examples/loadtest/checkpointer_contention.py` — N concurrent in-process runs, reporting
throughput, latency, CPU per run and checkpointer lock wait/hold. Run against `main` and the
branch; the A/B table is Run 6 in `load-and-scale.md`.
