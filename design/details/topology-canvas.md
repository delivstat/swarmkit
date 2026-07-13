---
status: accepted
---

# Topology canvas — one graph, two modes (edit + examine-run)

> **Implementation (M20).** Slice 2 (view-only graph) = task #16; slice 5 (edit on canvas) = task
> #17; slices 3 + 7 (examine-run + fleet read-only) = task #18. React Flow 19-compatible package is
> `@xyflow/react` (v12, the successor to `reactflow`). Auto-layout is a pure in-repo tidy-tree helper
> (`lib/topology-graph.ts`) — no `dagre`/`elkjs` dependency for the tree case; revisit only if a
> non-tree layout is needed.

Companion to `workspace-ui.md` (this is its "topology graph editor" slice, expanded) and reusing the
observability reads (`/observability/runs/{id}/trace`). A visual, node-and-edge canvas for a
workspace's topology that serves **both** design-time editing and run-time examination on the same
layout.

## The insight

A topology **is** a graph — agents are nodes, delegation (`children`) is edges. A run is an
**execution over** that graph — which agents fired, in what order, their tool calls, timing, tokens,
cost. So one canvas renders two modes:

- **Edit** (design-time): drag/add nodes (agents), draw edges (delegation), and edit a node via the
  schema-driven form already built (archetype/skills pickers, tooltips). YAML stays the escape hatch.
- **Examine** (run-time): overlay a run's trace onto the same layout — colour/annotate each node by
  what happened (fired vs. skipped, duration, cost, error), light the delegation path taken, and
  click a node → its `agent.step` span with tool calls + tokens + cost. Two sub-modes:
  - **Replay** (post-hoc): from a finished run's `/observability/runs/{id}/trace`.
  - **Live** (in-flight): stream a running job's events (`GET /jobs/{id}/stream`, SSE) so nodes light
    up **as the run executes** — the graph becomes a live view of the agents working. Crucially, when
    the run needs a human decision — an approval or input request (an executor
    `exec.approval_requested` / `exec.input_requested`, or any HITL review) — the node **pulses** and
    the request is answerable **inline on the graph**. So the canvas doesn't just watch a run, it
    *unblocks* it. (This is the visual seat for the executor abstraction's mid-run interaction,
    `executor-abstraction.md` §6.2–6.3 — same event stream, surfaced spatially.)

Examine is the standout: a flat waterfall answers *"what happened when"*; the graph overlay answers
**"where in the org did the time and money go"** — which is what an operator actually asks. It also
**unifies** the surfaces built separately — the topology Form view (design), the trace waterfall +
audit (monitor), and the approval inbox (HITL) — onto one spatial model.

### Beyond a single run

- **Aggregate / compare** — overlay stats across the last N runs instead of one: average cost/latency
  per node, failure rate, the consistent hot-spot. The fleet's *"which agent is expensive or flaky"*
  question, answered on the graph from metrics already exported (`swarmkit_agent_steps_total`,
  per-node cost).
- **Edit affordances beyond the agent graph** — the topology also carries `governance`, `planning`,
  and `synthesis` blocks that aren't nodes/edges. The canvas edits the graph; these render as
  side-panels (still schema-driven forms), so it's the graph *and* its non-graph config, not a lossy
  view.

## Goal

- A reusable `TopologyCanvas` component: renders a topology as an interactive graph; **edit** and
  **examine** modes; auto-laid-out (topologies are trees).
- Workspace UI: the composer's visual editor + a graph view of a run on the run-detail page.
- Fleet UI: the **same canvas, read-only, examine-only**, on the fleet run-detail — an operator sees
  a run on the topology graph instead of raw Jaeger spans.

## Non-goals

- Not a replacement for the YAML escape hatch or the schema-driven Form view — the canvas is a peer.
- Not a general diagramming tool; nodes/edges are agents/delegation, nothing else.
- Fleet UI gets **no edit** — you don't edit a remote instance's topology from the monitor.

## Architecture

- **React Flow** (`reactflow`) for the canvas — purpose-built for interactive node/edge graphs with
  custom node renderers, pan/zoom, controlled edges, and read-only overlays. Hand-rolled SVG would
  reinvent months of interaction + layout handling.
- **Auto-layout** via `dagre` (or `elkjs`): topologies are trees (`agent.children`), so a
  hierarchical top-down layout positions nodes deterministically; the user can nudge, but never has
  to hand-place.
- **Custom node** = an agent card (id, role icon, archetype, skill count) in edit mode; the same card
  with an execution overlay (duration bar, cost, status ring) in examine mode.
- Thin client, no new logic: both modes read existing endpoints (below). The canvas is **copied** into
  the fleet UI (per the copy-for-consistency decision — `control-plane-ui` is a private app), not
  imported across the app boundary.

## Data

- **Edit graph** — from `GET /api/topologies/{id}` (`topologyDetail.resolved`, the agent tree) →
  nodes; `agent.children` → edges. Edits round-trip through YAML (parse → canvas → serialize → save
  via the existing validate+reload path), so the canvas never holds a second source of truth.
- **Examine overlay** — from `GET /observability/runs/{id}/trace` (already shipped): the span tree
  `topology.run → agent.step.<agent_id> → tool.call.<name>`. Each `agent.step` span carries
  `swarmkit.agent.id` (→ map to the node), `duration_ms`, `swarmkit.model.tokens_in/out`,
  `swarmkit.model.cost_usd`; `tool.call` spans give per-tool timing. So the overlay needs **no new
  backend** — it maps spans onto nodes by `agent.id`.

Nodes present in the topology but absent from the trace = "did not fire" (dimmed); a click on any
node opens its span detail (the tool calls, tokens, cost) — the waterfall data, spatially.

## API shape

Reuses `GET /api/topologies/{id}` (+ `/yaml`, `saveTopology`) and `GET /observability/runs/{id}/trace`.
**No new endpoints anticipated.** (Open question: whether the examine view wants the topology *as it
was at run time* — for now it overlays the current topology; versioned-topology-at-run is a later
concern.)

## Sequencing (one design + PR per slice)

1. This note (design-only). **DONE.**
2. **View-only graph** — render the topology as a React Flow canvas (a new composer view). **SHIPPED
   (UI 0.7.0, PR #568, task #16)** — `lib/topology-graph.ts` (pure tidy-tree) +
   `components/topology-canvas.tsx`.
3. **Examine replay** — feed a finished run's trace onto the graph (node colour by
   cost/duration/status, dim did-not-fire) on the run-detail page. **SHIPPED (UI 0.8.0 / runtime
   1.95.0, PR #570, task #18)** — `lib/topology-run.ts::traceToOverlay`; `RunGraph` on `/jobs/[id]`;
   polls the trace so an in-flight run fills in. (`get_job` now returns `topology`.)
4. **Live run** — stream `/jobs/{id}/stream` so nodes light up in-flight, and surface approval/input
   requests inline on the pulsing node (the cockpit for HITL + executor interaction). Governance is
   unchanged — answering inline routes through the same approval gate; the canvas is just the surface.
   *(Deferred — examine currently polls the trace; live SSE + inline-answer is a follow-up.)*
5. **Edit on canvas** — add/remove nodes, draw delegation edges; node panel = the schema form.
   **SHIPPED (UI 0.8.0, PR #569, task #17)** — `lib/topology-edit.ts` (pure raw-YAML ops, round-trip)
   + editable `TopologyCanvas` + composer wiring. (`governance`/`planning`/`synthesis` side-panels
   deferred.)
6. **Aggregate / compare** — per-node stats across the last N runs (cost/latency/failure hot-spots),
   from the exported metrics. *(Deferred.)*
7. **Fleet port** — copy the read-only view + replay into the fleet UI run detail (no edit). Needs a
   federated per-run **trace** endpoint (panel proxy → instance `/observability/runs/{id}/trace`);
   the fleet UI today federates only `RunRow` cost rows + a Jaeger link. *(Follow-up.)*

## Test plan

- Pure helpers (unit): `topology → {nodes, edges}` (tree flatten, incl. nested children); `trace →
  node-overlay map` (span `agent.id` → {duration, cost, tokens, status}; missing node = not-fired).
- Round-trip: canvas edit → YAML → reload preserves structure (golden fixture).
- E2E (local Playwright runner, not CI): load a topology → graph renders; run it → open examine →
  the fired nodes light up with cost/duration; click a node → its tool calls.

## Demo plan

Per slice: view → a topology renders as a graph. Examine → a real run's trace lights the path with
per-node cost/duration; the expensive node is obvious at a glance. Edit → add an agent + a delegation
edge on the canvas and save. Fleet → the same run graph in the fleet run-detail, read-only.
