# The dashboard shows what the workspace is actually doing

**Status:** implemented (runtime 1.156.0, UI 0.33.0, webui 0.10.0)

## Goal

Make the dashboard answer the questions an operator has — what ran, what broke, what it cost, where
it came from, what needs me — from data the workspace already records.

## Non-goals

- No new telemetry. Every number below comes from rows that were already being written, or that
  should have been.
- No charting library. Counts and short ranked lists; a dashboard card is not a report.
- No all-time totals. "142 runs since the workspace existed" is a fact nobody acts on.

## The gap

Three layers, each hiding the one below.

**1. The page read the wrong store.** It used `/jobs` — the in-memory `JobStore`, holding only what
the current serve process started via `POST /run/{topology}`, emptied on restart. A workspace driven
from the CLI, a pipeline or chat showed an empty dashboard, and a restart erased whatever was there.
The numbers were not stale; the source was wrong.

**2. `/usage` answered for one path in four.** `_record_run_usage` writes *both* usage sinks — one
`run_usage` row per model, which is the only thing feeding `/usage` and `/usage/{job_id}`, plus the
job-level totals. It lived inside `server/_jobs.py`, so only `POST /run/{topology}` could reach it.
As the other paths gained job records — CLI 1.150.0, pipeline stages 1.152.0, chat 1.155.0 — each
hand-rolled the job-level half and wrote no per-model rows. The per-model cost breakdown was
therefore blank for three of the four ways to run a topology.

**3. The three copies recorded real runs as free.** They read `usage.cost_usd` directly. Providers
that report only tokens — Ollama, most local runners — leave that at zero, and the price-table
estimate in the real recorder is what turns those into a cost.

## Design

**One recorder, in `persistence/_usage_recording.py`.** `usage_fields(usage, job_id, store)` returns
the job-row columns *and* writes the per-model rows on the way. All four paths call it. A test
states this against the sources — any recorder that reintroduces `fields["usage_input_tokens"]`
fails — because the failure mode is a *new* path copying the totals and skipping the breakdown.

**A `source` column on `jobs`** — `serve | cli | pipeline | chat`. Two reasons: the dashboard cannot
otherwise say where work comes from, and `correlation_id` is ambiguous now that a pipeline run and a
chat conversation both use it, with ids that are not reliably distinguishable. Null for rows written
before the column existed; those show as *unattributed* rather than being guessed into a bucket.

**The jobs migration is list-driven.** It previously checked for `correlation_id` and returned, so
`source` would have been silently skipped on every database that already had it — the second
additive column was going to be the first one to break.

**Unrecorded stays distinct from zero, everywhere.** No finished runs means *no* failure rate — null
rendered as `-`, not `0%`, which would claim everything is fine. A workspace that has not run shows
`-` for spend; one whose providers report no cost shows `$0.00`. `<$0.01` rather than `$0.00` for a
real but small cost.

**The failure rate is over finished runs**, not all runs, or a burst of in-flight work makes a
failing workspace look healthy.

## Surface

`/jobs/history` gains `source`. The dashboard shows: activity for a chosen window (runs, in flight,
failed, failure rate, spend, tokens), what is waiting on a human, runs and spend by source, spend by
topology with failure counts, and recent failures linking to their job pages.

## Test plan

`test_every_run_path_records_usage.py` — both sinks written, rows carry the job id, a token-only
provider is priced rather than recorded as free, a provider-reported cost wins, bookkeeping failures
never raise, and the two source-level properties (no path hand-rolls the fields; every path names
its source). `lib/dashboard.test.ts` — the arithmetic plus the distinctions that must not be
flattened.

## Demo

`packages/runtime/demos/dashboard_data.py` — runs work through several paths and prints what the
dashboard would show.
