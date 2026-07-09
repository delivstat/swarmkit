# 23 — usage pull-on-sync

Status: accepted. Extends the observed-state cache (doc 19 Phase 1) to also pull each Mode-A
instance's usage rollup, so the fleet Runs page reflects real instances without requiring the
observability push pipeline.

## Goal

The fleet **Runs** page reads `usage_rollup()` — token/cost totals grouped by model across the
fleet. Today those rows exist only if instances **push** usage events to `POST /aggregate/usage`
(the D2/D3 hybrid: state pulled, events pushed). An instance that has run workloads but isn't wired
to push shows nothing on the Runs page, even though `serve` already exposes the totals at `GET
/usage`.

This closes that gap for directly-reachable (Mode A) instances: fold a usage pull into the existing
`/sync` path, so `sync` refreshes both the artifact inventory **and** the usage rollup in one call.

## Non-goals

- **Not** replacing the push path. Mode B (NAT'd) instances can't be pulled; they keep pushing.
  Pull-on-sync is additive and Mode-A-only, exactly like `/sync` itself.
- **Not** pulling per-event usage history — only the cumulative per-model rollup `serve` already
  computes (`GET /usage` → `by_model`). Events stay push-only.
- **Not** evals or audit. `serve` exposes no `/evals` pull endpoint; audit is push-only. Out of
  scope (see the "what can't be synced" table in the PR).

## Why a snapshot, not `ingest`

`AggregationStore.ingest` is **append-only + dedup-by-`(instance_id, kind, record_id)`** with
`ON CONFLICT DO NOTHING` — correct for at-least-once *event* pushes. A pulled `/usage` rollup is a
**cumulative snapshot**, not an event: re-syncing must *refresh* the totals, not dedup them away.
Pushing a snapshot through `ingest` would freeze the totals at the first sync.

So we add `AggregationStore.put_usage_snapshot(instance_id, by_model)`: one row per
`(instance_id, "usage", "pull:<model>")` written with `ON CONFLICT DO UPDATE` (replace-on-sync). The
reserved `pull:<model>` record-id prefix keeps pulled snapshots distinct from pushed event ids, and
the existing `usage_rollup()` (group by model+provider, sum tokens/cost) folds them in unchanged.

**Double-count caveat:** an instance that *both* pushes usage events *and* is pulled would have its
totals counted twice. In practice an instance is one or the other — pull is Mode-A-only, push is the
Mode-B mechanism — so this doesn't arise for a given instance. Documented, accepted for v1.

## API shape

- `connector.fetch_usage(endpoint, token_ref) -> dict` — `GET /usage`, returns `{summary, by_model}`.
  Raises `ConnectorError` on failure (same contract as `fetch_state`).
- `AggregationStore.put_usage_snapshot(instance_id, by_model) -> {"written": int}` — replace-on-sync
  per-model rows. `by_model` rows carry `model, calls, input_tokens, output_tokens, cost_usd`
  (`serve`'s shape); `provider` is absent from `serve` and stored as `null`.
- `POST /instances/{id}/sync` response gains `"pulled_usage": <int>` — models written, or `0` when
  the usage pull failed (best-effort; the state sync is the contract and is never failed by a usage
  hiccup).

## Best-effort

State is the contract; usage is additive. If the usage pull raises `ConnectorError`, `/sync` logs it,
sets `pulled_usage: 0`, and still returns the state delta. A usage hiccup never fails a sync.

## Test plan

- `put_usage_snapshot` writes one row per model; a second call with new totals **replaces** (not
  duplicates) — `usage_rollup()` reflects the latest, not the sum.
- `fetch_usage` parses `{summary, by_model}` and raises `ConnectorError` on non-200 (fake serve).
- `/sync` route: with a stub `fetch_usage`, the response carries `pulled_usage` and the rollup shows
  the instance's models; a raising `fetch_usage` yields `pulled_usage: 0` and a successful state sync.

## Demo

`examples/` sync against a live `serve` with recorded usage → Runs page shows real per-model totals
pulled at sync, no push configured. Transcript in the PR.
