---
status: draft
---

# Topology canvas — one graph, two modes (edit + examine-run)

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
  click a node → its `agent.step` span with tool calls + tokens + cost.

Examine is the standout: a flat waterfall answers *"what happened when"*; the graph overlay answers
**"where in the org did the time and money go"** — which is what an operator actually asks. It also
**unifies** the two surfaces built separately — the topology Form view (design) and the trace
waterfall (monitor) — onto one spatial model.

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

1. This note (design-only).
2. **View-only graph** — render the topology as a React Flow canvas (a new composer view, or replacing
   "structure"). Prerequisite; lowest risk.
3. **Examine overlay** — feed a run's trace onto the graph (node colour by cost/duration/status, path
   taken, click → span detail) on the run-detail page. *Highest value; zero new backend.*
4. **Edit on canvas** — add/remove nodes, draw delegation edges; node panel = the schema form.
5. **Fleet port** — copy the read-only view+overlay into the fleet UI run detail.

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
