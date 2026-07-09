# 24 — instance-scoped observability + federated per-run detail

Status: accepted. The fleet observability pages (Runs / Evals / Audit) gain a per-instance scope, and
the Runs page gains **per-run detail** (individual runs with cost) sourced by federation, not storage.

## The two-lane model

A deliberate split, so the panel holds no more than it should (extends the D2/D3 hybrid + doc 23):

| Data | Mechanism | Where it lives | Availability |
| --- | --- | --- | --- |
| **Aggregates** — cost/usage/eval rollups | **Push** (`POST /aggregate/*`) | panel (AggregationStore) | always; Mode A **and** B; survives the instance going offline |
| **Details** — individual runs, cost-per-run | **Federate** (`GET /instances/{id}/runs` → serve `/jobs/history`) | the instance owner's server | live, on demand; **never stored** on the panel |

Rationale — **privacy line.** Granular per-run history (topology, timing, tokens, cost) is operational
metadata that stays on the owner's instance; the panel keeps only aggregates. This matches the
existing rule (`AggregationStore`: *"Raw traces/metrics stay in a BYO OTel collector … live jobs are
federated (queried on demand), not stored"*). Per-run history is on the "raw/federated" side of that
line. Note `/jobs/history` carries **no prompt/response content** — only run metadata.

## Federated `/runs` — the reachability envelope

`GET /instances/{id}/runs` always returns **200** with `{reachable, reason, runs}` so the UI can tell
the three states apart without inferring from an error:

- reachable + runs → `{reachable: true, reason: null, runs: [...]}`
- reachable, none yet → `{reachable: true, reason: null, runs: []}`
- **Mode-B (poll)** → `{reachable: false, reason: "poll-mode", runs: []}` — a NAT'd instance can't be
  federated; its pushed aggregate cost still shows, so this is a limitation, not an error.
- **unreachable** (direct instance didn't answer) → `{reachable: false, reason: "unreachable", runs: []}`
  and the instance's health flips to `unreachable`.

`404` only for an unknown instance (a genuine client error). A per-run record carries serve's
`/jobs/history` shape: `job_id, topology, version, status, created_at, completed_at,
usage_input_tokens, usage_output_tokens, usage_cost_usd`.

## Instance scope on the pushed aggregates

`usage_rollup`, `eval_summary`, `recent_audit` gain an optional `instance_id` (fleet-wide when
omitted). The routes `/usage`, `/eval`, `/audit` accept `?instance_id=` and pass it through. Every
`agg_record` already carries `instance_id`, so scoping is a `WHERE`, not a schema change.

## API shape

- `connector.fetch_runs(endpoint, token_ref) -> list` — `GET /jobs/history`.
- `GET /instances/{id}/runs -> {reachable: bool, reason: str|null, runs: list}`.
- `GET /usage|/eval|/audit?instance_id=<id>` — scoped rollups (omit → fleet-wide).

## Non-goals

- **No per-run storage.** Runs are federated live; nothing persisted. (If Mode-B per-run detail is
  ever needed, that's a push path — out of scope; Mode-B shows aggregate only.)
- **Artifacts stays fleet-wide** — the deployable registry is not instance-scoped; per-instance
  artifacts remain the instance detail's Inventory (cached `/fleet/state`).

## Test plan

- `/runs`: reachable+runs (cost present), reachable+empty, poll-mode, unreachable (+health flip), 404.
- Store: `usage_rollup/eval_summary/recent_audit` scope to one instance; fleet-wide when omitted.
- Route: `/usage?instance_id=` narrows; fleet-wide sums all.

## Demo

`GET /instances/{id}/runs` against a live `serve` with recorded runs → per-run cost list; against a
poll instance → `reachable:false`. Transcript in PR B (the UI) shows the searchable per-run table +
the "instance unavailable" state.
