---
status: accepted
---

# Fleet read-only run graph (federated per-run trace)

The workspace UI can already render a finished run as a graph — the topology canvas in **examine
mode** overlays a run's per-agent cost/duration/status onto the topology (`traceToOverlay`, fed by the
runtime's `GET /observability/runs/{run_id}/trace`). The **fleet** surface (`packages/control-plane-ui`)
lists runs across instances but can't show that graph, because a run's trace lives on the instance
that owns it and the control plane has no federated per-run trace endpoint. This note closes that gap
(task #25).

## Goal

From the fleet UI, open any completed run on any *directly-reachable* instance and see the same
read-only run-over-topology graph the workspace UI shows — green fired, red errored, dimmed didn't
fire — without the control plane ever **storing** the trace.

## Non-goals

- Not editing (fleet run graph is strictly read-only — the fleet UI is a media/observability surface).
- Not a live cockpit (SSE streaming / inline HITL) — this is the *finished-run* graph only.
- Not aggregate-across-runs stats — that's the pushed-aggregates lane, separate.
- Not trace storage on the panel — per the two-lane model (design §24), granular per-run detail stays
  on the owner instance and is fetched **live, on demand**.

## Design — two slices

### Slice 1 — federated trace endpoint (this note's first PR)

Mirror the existing federated `runs` proxy exactly (`_mount_instance_runs` / `fetch_runs`):

- **Connector** `fetch_run_trace(endpoint, token_ref, run_id) -> dict | None` — GET the instance's
  `/observability/runs/{run_id}/trace`; a real **404** (no trace recorded for that run) returns
  `None`, any connection/auth failure raises `ConnectorError`.
- **Route** `GET /instances/{instance_id}/runs/{run_id}/trace` → the same reachability envelope the
  runs route uses:
  - unknown instance → 404;
  - Mode-B (poll) instance → `{reachable: false, reason: "poll-mode", trace: null}` (can't be pulled);
  - unreachable direct instance → `{reachable: false, reason: "unreachable", trace: null}` (+ flips
    health), matching `instance_runs`;
  - reachable, has trace → `{reachable: true, reason: null, trace: <span tree>}`;
  - reachable, no trace for that run → `{reachable: true, reason: "no-trace", trace: null}`.
- **Type** `RunTraceFn = Callable[[str, str, str], Awaitable[dict | None]]`, injected in the app
  factory like `RunsFn` (so tests pass a fake).

The span-tree shape is exactly what the instance returns (`topology.run → agent.step → tool.call`), so
the panel forwards it verbatim — no reshaping, no schema drift.

### Slice 2 — the fleet run-graph view (follow-up PR)

`packages/control-plane-ui` is a **standalone** Next.js app that does not depend on `@swarmkit/ui` or
the runtime (fleet-panel-standalone principle). So the graph rendering is **ported**, not imported:
copy the three pure helpers (`topology-graph`, `topology-run`/`traceToOverlay`, and a read-only
`TopologyCanvas`) into control-plane-ui and add `@xyflow/react`. They're pure + already unit-tested in
`@swarmkit/ui`; a contract test (not a shared import) keeps the two copies honest, consistent with how
runtime↔panel dedup is handled. The run detail page (`app/runs`) gains a "graph" view that fetches the
federated trace + the topology structure (via the instance's topology-detail read) and renders the
examine overlay. Reachability states (poll-mode / unreachable / no-trace) surface as a plain message,
not a spinner.

## Test plan

- **Slice 1 (unit, control-plane):** federated trace returns the span tree for a direct instance; a
  Mode-B instance → `poll-mode`; an unreachable instance → `unreachable` + health flip; a 404 from the
  instance → `reachable: true, reason: "no-trace"`. Fake `RunTraceFn`, no network (mirrors the runs
  test).
- **Slice 2 (unit, control-plane-ui):** the ported `topology-graph`/`traceToOverlay` helpers pass the
  same assertions as their `@swarmkit/ui` originals (the contract test); the run-graph view renders
  the three reachability states.

## Demo

Fleet UI → an instance's runs → open a completed run → the run-over-topology graph with the examine
overlay. A poll-mode instance shows "live per-run detail unavailable for NAT'd instances" instead.
