---
title: The audit is a write-through journal
description: Persist each audit event the moment it is recorded, not in one batch at the run boundary, so a run that crashes (SIGKILL, OOM, power loss) still leaves the trail up to the crash.
tags: [audit, observability, durability, governance]
status: accepted
---

# The audit is a write-through journal

## The gap

Until 1.239.0 audit events were held in the governance provider's in-memory list for the whole
run and written to the store in one batch at `_end_run`. `GET /events` — "the durable log an
application reconciles from" — reads the same `audit_events` table, so both surfaces depended on
the run reaching its boundary. A `kill -9` mid-run, an OOM, or a power loss wrote **nothing**:
`test_kill9_recovery.py` shows the store empty right after a SIGKILL, while the LangGraph
checkpoint (written per node) survived and the run resumed. Recovery was durable; the record of
what happened before the crash was not.

That inverts what an audit log is for. The append-only invariant (§8.3) is about *integrity* — no
agent can update or delete an entry — and it was never violated. Durability was simply never
stated, so batch-at-end (one pass, one redaction, no per-event write on a hot run) looked free.
It is not free for the run a security team most wants to reconstruct: the one that crashed.

## Goal

Each recorded event reaches the durable store when it is recorded. A hard kill loses at most the
single in-flight event, not the run. `GET /events` and `swarmkit logs` show a crashed run's trail
up to the crash. No new public API; the change is where the write happens, not what is written.

## Non-goals

- Not per-event `fsync`. The floor is "written to the store before the next event", not "flushed
  to disk before the operation the event describes returns". SQLite WAL and Postgres both make
  the row visible to a reader immediately; surviving a kernel-level power cut mid-fsync is a
  storage-engine concern, not this seam's.
- Not a second store. The journal writes to the same `AuditProvider` the batch used.
- Not removing the end-of-run pass. It stays as a completeness safety net (see below), made a
  no-op for already-journaled events by the store's existing id dedup.

## Design

Three facts already in place make this small:

1. **`AuditEvent` stamps `event_id` (uuid4) and `run_id` at construction** from the per-task run
   scope. So an event is self-identifying and self-attributing the moment a node creates it.
2. **`AuditProvider.record` dedups on `event_id`** (a duplicate primary key returns quietly, per
   the ABC). So writing the same event twice is safe and idempotent.
3. **The runtime owns both** the governance provider and the audit provider, and constructs the
   provider it hands the compiler — the right place to interpose.

The seam is a wrapper, `JournalingGovernance` (`audit/_journal.py`), installed by the runtime
around the base provider:

```
record_event(event):
    await base.record_event(event)      # unchanged: in-memory list, trust, flight recorder
    await write(event)                  # NEW: redact, stamp topology, persist NOW
                                        # failure is logged, never raised — the run continues and
                                        # the end-of-run batch is the retry
```

`write` is the runtime's `_journal_write`: it applies the same per-skill redaction the batch
applied (`_redact_payload`, factored out of `_apply_skill_redaction`), stamps the topology from a
new `current_topology` run-scope var (the event's own `run_id`/`labels` are already set), and
`dataclasses.replace`s the event with the redacted payload — keeping `event_id`, so the store
dedups it against any later write.

The end-of-run path is unchanged except that `RunEvent` now carries `event_id` and
`_persist_events_to_audit` reuses it instead of minting a fresh one. That turns the batch into a
**completeness net**: an event the journal missed (a write that raised, an event recorded outside
run scope) is still written at the end, while every event the journal already wrote is deduped.
The journal is the durability mechanism; the batch is the backstop.

### What a crash now leaves

- Events recorded before the crash: **in the store**, redacted, attributed to the run.
- The event in flight at the instant of the kill: possibly lost (its `record` may not have
  returned). One event, named by what came after it on resume.
- The LangGraph checkpoint: durable as before, so the run still resumes.

### Cost

One store write per event instead of one batch per run. Audit volume is a handful of events per
agent step, and SQLite/WAL and Postgres both take a single-row insert cheaply. If a
high-throughput deployment needs it, a bounded coalescing buffer (flush every N events or T ms) is
a later tuning of `_journal_write` alone — the write-through default is what an audit log should
ship with.

## Test plan

- `test_audit_journal.py` — a two-node run; assert the audit store has each event *before* the run
  ends (drive record, read store mid-run via a spy); a failed journal write is logged and the
  end-of-run batch still persists the event; the batch does not duplicate a journaled event.
- `test_kill9_recovery.py` — updated: after the SIGKILL the store now holds the pre-kill events
  (this is the assertion that flips — the note it referenced is this one), and the resumed run's
  record joins them without duplication.
- Existing audit/observability suites unchanged: same events, same redaction, same `GET /events`.

## Demo plan

`just demo-...` not needed; the kill-9 test is the demo. `docs/site/guides/evaluating-the-failure-path.md`
(the eight-test evaluation) cites this note for Test 7/8, now answered "the trail survives".
