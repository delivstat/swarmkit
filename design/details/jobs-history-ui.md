# The jobs page reads both stores

Status: implemented (@swarmkit/ui 0.30.0). Small UI fix; shape approved in conversation
(two sections, live above history).

## The gap

`/jobs` is the in-memory `JobStore` — "Thread-safe in-memory job store", this serve process only,
empty after a restart. It was the page's sole source, so the page showed nothing but in-flight work
and lost the visible record of every run whenever serve restarted.

`/jobs/history` had existed server-side the whole time and nothing called it. It reads the durable
store and carries what the in-memory one does not: `usage_input_tokens`, `usage_output_tokens`,
`usage_cost_usd`.

## Design

Two sections: **Running now** above **History**.

- *Running now* — from `/jobs`, filtered to `pending` / `running`, polled every 3s.
- *History* — from `/jobs/history`, polled every 15s (durable rows change only when a job starts or
  finishes, and the table can be long), with tokens and cost.

### The overlap is the only subtle part

A job is written to **both** stores at creation (`_services.py` persists it as it starts), so while
it runs it appears in both lists. Two tables built naively would print every running job twice.
History therefore excludes anything currently shown live.

The same fact means nothing is lost the other way: a job that finishes leaves the live section and
is already in history, because it was persisted at the start.

### Small honesty details

- **Cost shows `-` when unrecorded**, never `$0.00` — an unmeasured run and a free one are
  different, and a confident `$0.00` is the sort of blank this codebase keeps getting bitten by.
- **`<$0.01`** rather than rounding a real cost away to zero.
- The two sections poll independently, so one erroring does not blank the other.
- The empty history state names what is required (`storage.runtime`) rather than implying there
  have been no runs — the endpoint returns `[]` both when there is no history and when no durable
  store is configured, and the client cannot tell those apart.

## Test plan

Logic lives in `lib/jobs.ts` and is tested in `lib/jobs.test.ts` — the repo's convention is pure
functions under `lib/`, since there is no component-testing setup:

- durable history is shown at all (the bug)
- a running job is not printed twice
- a finished job stays in history once it leaves the live list
- `pending` counts as running; a completed in-memory job is not claimed by the live section
- newest running job first (`/jobs` returns oldest-first)
- either source being unavailable does not blank the other
- cost/token/timestamp formatting, including the unrecorded-vs-zero distinction

The server contract was verified against a real `Store`: the `/jobs/history` payload keys match the
UI's `PersistedJob` exactly.

## Demo

`just demo-jobs-history`.
