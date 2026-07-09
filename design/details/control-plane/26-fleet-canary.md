# 26 — fleet canary deployments

Status: accepted, built in slices. Surface the runtime's canary machinery in the fleet control
plane: monitor a canary, promote/roll it back, and (eventually) initiate a canary rollout — all from
the panel + UI, across the fleet.

## What already exists (runtime)

`CanaryRouter` (design/details/canary-deployments.md) does weighted traffic-splitting between
topology versions, tracks per-version metrics (error rate, drift), supports auto-promotion, and
serve exposes:

- `GET /canary` — status + per-version metrics
- `POST /canary/{topology}/promote` (body `{version}`) and `POST /canary/{topology}/rollback`

It's configured from `workspace.yaml` `server.canary` at startup (and is `None` when unconfigured).
The gap is purely fleet-side: the panel can't read or drive any of it.

## The two layers

**Layer A — monitor + control (PR 3a).** Federate the read + promote/rollback through the panel,
exactly like `/jobs` federation (Mode A, reachability envelope). UI gets a canary card with
per-version metrics and promote/rollback buttons.

- connector: `fetch_canary`, `promote_canary`, `rollback_canary`.
- panel routes: `GET /instances/{id}/canary` → `{reachable, reason, canary}`;
  `POST /instances/{id}/canary/{topology}/promote` (`{version}`) and `.../rollback` (manage scope).
- UI: `CanaryCard` on the instance detail — status, metrics, promote/rollback.

**Layer B — initiate a canary rollout (PR 3b runtime, PR 3c panel+UI).** Deploy a *new* version to an
instance *as a canary* — split X% of traffic to it, watch it, promote or roll back. This needs a
**runtime** addition (the current router is static + `None`-when-unconfigured):

- **3b (runtime):** `CanaryRouter.start_route(topology, base_version, canary_version, weight,
  promote_when)` that adds/updates a route on the live router (bootstrapping the router when it was
  `None`); serve `POST /canary/{topology}` to drive it. The canary version's artifact must already be
  deployed to the instance (existing deploy path), then this splits traffic to it.
- **3c (panel + UI):** a panel "canary deploy" = push the version (existing signed deploy, design 22)
  **then** call the instance's canary-start with the weight; UI controls to pick version + weight.

## Ordering + why

3a is self-contained and immediately useful (the runtime endpoints exist), so it ships first. 3b/3c
build the rollout-initiation on top. Each slice is its own PR with tests + a demo.

## Invariants

- Promote/rollback and canary-start are **manage-scope** operations (design 20) — same gate as
  deploy. Monitor (`GET /canary`) is monitor-scope.
- Federation is **Mode A only** (a NAT'd poll instance returns `reachable:false`, like `/runs`).
- Canary-start (3b) reconfigures live routing but does **not** persist to `workspace.yaml` — a
  restart reverts to the declared config (documented; persistence is a later question).

## Test plan (per slice)

- **3a:** connector fns; panel routes (reachable/poll-mode/unreachable envelope; promote/rollback
  pass-through + manage-scope gate); UI CanaryCard (renders metrics, promote/rollback call the API,
  unavailable states).
- **3b:** `start_route` adds a route / bootstraps a `None` router / rejects unknown base version;
  serve `POST /canary/{topology}` happy + error paths.
- **3c:** panel canary-deploy orders push-then-start and rolls back intent on failure; UI deploy
  controls.

## Demo (per slice)

3a: federated canary status + promote against a serve with a configured canary route. 3b: start a
canary at runtime and watch traffic split. 3c: end-to-end canary deploy from the panel UI.
