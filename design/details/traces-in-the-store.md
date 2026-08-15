---
title: Consolidate run traces into the store
description: Run traces are JSON files under .swarmkit/traces/, the only observability data that does not follow storage.runtime. On a fleet they stay on whichever host ran them, they are the sole record of cost for a CLI run, and nothing prunes them. This proposes a `traces` StoreKind that inherits storage.runtime, with the files kept as the fallback.
tags: [runtime, observability, persistence, storage]
status: draft
---

# Consolidate run traces into the store

**Scope:** `runtime` (`trace.py`, `persistence/`, `_workspace_runtime.py`, observability CLI)
**Design reference:** §14.5 (audit + observability). Builds on `storage-service.md`,
`postgres-backend.md`, `runtime/usage-recording-and-cost`.
**Status:** draft — not scheduled. Recorded so the reasoning survives.

## Goal

Make a run's trace land wherever the workspace's storage is configured, so cost and token data
consolidates with everything else instead of being stranded per-host.

## Non-goals

- **Not replacing OTel export.** `_finalize_trace` already mirrors to a collector; that stays the
  path for dashboards. This is about SwarmKit's own queryable record.
- **Not changing the trace shape.** `RunTrace` / `TraceSpan` stay as they are.
- **Not moving prompts.** The local prompt ring buffer (`prompts.sqlite`) is deliberately
  machine-local and stays that way — and it remains the unredacted surface for raw local
  inspection once traces are redacted.
- **Not a migration of existing trace files** in the first slice — read-through covers them.

## Why this is worth doing

`trace.save()` writes `{workspace}/.swarmkit/traces/{run_id}.json`. There is no trace table, and
`StoreKind` has no `traces` member — so traces are the **only** observability data that does not
follow `storage.runtime`. Three consequences, in increasing order of cost:

1. **A fleet cannot see its own runs.** Audit, jobs, usage, sagas and governed memory all
   consolidate into Postgres; traces stay on the host that ran them. The fleet panel federates
   per-run cost from the jobs table and simply has no trace to offer.
2. **For a CLI run, the trace is the *only* record of cost.** `run_usage` has exactly one writer —
   the serve job path — so `swarmkit run` contributes nothing to `/usage`. Its tokens and dollars
   exist solely in that JSON file. Anyone driving work through the CLI has a cost blind spot that
   consolidating traces would close on its own.
3. **Nothing prunes them.** `storage.audit.retention_days` bounds the audit; traces accumulate
   forever, one file per run, in a directory nobody sweeps.

## The plan

### 1. A `traces` StoreKind

```python
class StoreKind(StrEnum):
    ...
    TRACES = "traces"
```

It **inherits `storage.runtime`** — it is application data, not a component with its own driver
requirement, so it is emphatically not in `_NO_INHERIT` the way `CHECKPOINTS` is. Its SQLite file is
`store.sqlite`, coexisting with jobs and usage rather than adding another file.

### 2. One table, JSON payload

```python
run_trace = Table(
    "run_trace", metadata,
    Column("run_id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("topology", Text, nullable=False, default=""),
    Column("started_at", Text, nullable=False),
    Column("total_input_tokens", Integer, nullable=False, default=0),
    Column("total_output_tokens", Integer, nullable=False, default=0),
    Column("total_cost_usd", Float, nullable=False, default=0.0),
    Column("payload", Text, nullable=False),   # the full RunTrace as JSON
)
```

Totals are promoted to columns so cost is queryable without parsing every payload — the fleet
question is "what did last week cost", and that must not be a full scan of JSON blobs. The payload
stays whole so the span tree needs no schema migration when `TraceSpan` grows.

### 3. Write to the store, keep the file

`_finalize_trace` writes both for one release: the store becomes the source of truth, the file stays
so an operator's existing tooling and any `swarmkit trace` invocation against an older workspace
keep working. Both writes are best-effort — **telemetry must never fail a run**, which is already
the rule for the OTel mirror.

### 4. Read-through, newest first

`swarmkit trace` and `/observability/runs/{id}/trace` try the store, then fall back to the file. That
covers every trace written before this lands with no migration step, and makes the transition
invisible.

### 5. Retention

`storage.traces.retention_days`, defaulting to the audit's. A trace is bulkier than an audit event
and less often read, so unbounded growth is the worse default. Pruning deletes the row; the file
sweep is a separate, opt-in chore because deleting an operator's files is not something to do
implicitly.

## API shape

```python
# persistence/_store.py
def record_trace(self, trace: RunTrace, *, workspace_id: str, topology: str) -> None: ...
def get_trace(self, run_id: str) -> RunTrace | None: ...
def list_traces(self, *, limit: int = 50, since: str | None = None) -> list[TraceSummary]: ...
def usage_by_run(self, *, since: str | None = None) -> list[RunCost]: ...   # closes the CLI gap
```

```yaml
storage:
  traces:              # optional; inherits storage.runtime when absent
    retention_days: 90
```

## Redaction — decided: yes, and through the audit's policy

Confirmed by inspecting what a trace actually holds, because the scope is narrower than "the
payload" and that matters:

| field | content | needs redaction |
| --- | --- | --- |
| `ToolCall.arguments` | **the full argument dict** | **yes** |
| `ToolCall.result_length`, `AgentStep.result_length` | lengths only | no |
| everything else | ids, models, timings, tokens, cost | no |

Tool **results** are not in the trace — only their length — and prompts never were. So exactly one
field carries content, and it is the one where a credential or a customer record shows up.

Four decisions follow:

1. **Redact at WRITE time, never at read.** The whole reason this matters is that a shared Postgres
   widens who can read a trace; redacting on the way out would mean the secret was already stored.
2. **Reuse the audit's policy — do not add `storage.traces.redact`.** `audit/_redact.py` already
   resolves per-skill `redact:` pointers plus the workspace `audit.level`, and a span is produced by
   a skill. Two policies would drift, and the drift is one-directional and silent: a pointer added
   to the audit config, not mirrored into a trace config, reads as "redacted" while the trace keeps
   leaking. One policy, applied once, before either sink.
3. **The local file gets the same treatment as the store.** Tempting to keep the file raw for
   debugging, but the file is what gets tarred up and attached to a bug report — the moment it
   leaves the host it is no longer the trusted local artifact it was written as. Uniform is also
   simply easier to reason about than "redacted here, raw there".
4. **`swarmkit debug` stays the unredacted local surface.** The prompt ring buffer
   (`prompts.sqlite`) is already documented as never leaving the machine, and it is the right home
   for raw inspection. Redacting traces does not remove the capability; it moves it to the surface
   built for it.

**The OTel path needs no change.** `telemetry/_tracer.py` sets metadata only — status, error type,
provider, token counts — and zero content-bearing attributes. It does not carry `arguments`, so it
was never leaking and this does not widen it.

The cost of getting this wrong is asymmetric: an over-redacted trace is an annoying debugging
session, an under-redacted one is a credential in a shared database with an unknown reader set. So
the default follows the workspace `audit.level` and errs toward the audit's existing posture rather
than inventing a more permissive one for traces.

## Test plan

- **Unit.** Round-trip a `RunTrace` through the store; totals land in their columns and match the
  payload; a trace with no spans still records.
- **Read-through.** A trace present only as a file is still found by `swarmkit trace`; one present
  in both prefers the store; one in neither reports not-found rather than raising.
- **Backends.** Same assertions against SQLite and Postgres (the `postgres` marker pattern the
  store tests already use).
- **Best-effort.** A store that raises on write does not fail the run — assert the run result is
  unchanged and a warning is logged.
- **The CLI gap.** After a `swarmkit run`, `usage_by_run` reports that run's cost. This is the
  outcome that motivates the change, so it gets a test rather than an inference.
- **Retention.** Pruning removes rows past the window and leaves the files alone.
- **Redaction.** A workspace `redact: ["$.api_key"]` blanks that key in `ToolCall.arguments` in BOTH
  the stored row and the file; a trace written under `audit.level: minimal` carries no raw
  arguments at all; and the redacted value never appears in the persisted JSON — asserted by
  searching the written payload for the secret, not by trusting the policy call. The OTel export is
  asserted to remain metadata-only, so a future attribute carrying content fails this test.

## Demo plan

`just demo-traces-store` — run a topology twice against a Postgres-backed workspace, then show
`swarmkit trace` reading from the store and a `usage_by_run` total covering **CLI** runs, which
`/usage` reports as zero today.

## Open questions

- **One row or one row per span?** A JSON payload is simple and matches how traces are consumed
  (whole). Per-span rows would make "which agent burned the tokens" a SQL question instead of a
  parse. The totals columns are a deliberate middle; if span-level querying is wanted later, that is
  a second table, not a reshape of this one.
- **Fleet federation.** Once traces are in Postgres the fleet panel could federate them like jobs.
  Worth confirming that is wanted before sizing it — it changes the panel from summaries to
  potentially large payloads.
- **One row or one row per span?** A JSON payload is simple and matches how traces are consumed
  (whole). Per-span rows would make "which agent burned the tokens" a SQL question instead of a
  parse. The totals columns are a deliberate middle; if span-level querying is wanted later, that is
  a second table, not a reshape of this one.
- **Fleet federation.** Once traces are in Postgres the fleet panel could federate them like jobs.
  Worth confirming that is wanted before sizing it — it changes the panel from summaries to
  potentially large payloads.
