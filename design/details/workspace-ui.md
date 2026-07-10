---
status: draft
---

# Workspace UI — a co-equal visual surface for one workspace

## Goal

A browser UI, served alongside `swarmkit serve`, that lets a user **see, monitor, run, and author**
one swarm workspace — conversational authoring as the front door, a schema-driven designer as the
refine/reference layer, and live monitoring reusing the OTel/jobs data we already emit. It is a
**client of the serve API**, not a second home for business logic.

## Posture (decided)

- **Co-equal peer to the CLI, not a replacement.** Invariant #8 stands: CLI chat remains the
  documented v1.0 on-ramp; this UI is an equal surface, not billed as *the* primary one. The CLI and
  the UI are both thin clients of `WorkspaceRuntime` via the serve API (the thin-interface principle,
  `feedback_cli_architecture`). §15.3 is **not** reversed.
- **Conversational front door; designer refines.** A new user lands on describe-intent chat; a swarm
  drafts the artifact; the schema-driven designer opens that draft to inspect/tweak every field.
  Authoring-first (`project_authoring_first`) stays true — the designer is the power/reference layer,
  not the default creation path.
- **Two apps, not one.** `packages/control-plane-ui` = the **fleet** view (many instances, remote,
  governance). This = the **workspace** view (one workspace, local, deep). They share components
  (runs table, trace links, artifact cards) but are separate apps with separate scopes. Do not merge.

## Non-goals

- Not reversing the on-ramp, not deprecating the CLI, not a fleet/multi-instance console.
- Not hand-coded forms per artifact type (see below), and not a new logic layer — the serve API is
  the single contract.
- Not a from-scratch build: `packages/ui` is already scaffolded (`composer/`, `dashboard/`, `chat/`,
  `jobs/`, `skills/`, `archetypes/`, `canary/`), and this builds it out.

## What already exists (so this is smaller than it sounds)

- **`packages/ui`** — 24 files, the directories above. Bones for exactly this.
- **Serve API (~70% of the surface)** — artifact CRUD (`GET/PUT/POST /api/topologies|skills|archetypes`
  + `/yaml`), `run`, `jobs` (+ `history`/`stream`/`usage`), **`conversations`** (chat), `validate`,
  `canary`, `capabilities`, `triggers`, `reload`. The UI is mostly a frontend against this.

## The technical crux — the designer is schema-generated

"Expose every property + option, with help and tooltips" is **not** hand-built. A single form engine
renders any artifact from the canonical JSON Schemas (`packages/schema`): each field → input by type,
enum → select, constraints → validation, and the schema **`description` → the tooltip**. Consequences:

- "every available property" is free and **stays in sync** — add a schema field, it appears in the
  designer with its docs (`project_schema_surface` + `schema-change-discipline`).
- Tooltips reuse the same `description` strings LLMs consume (`project_usability_and_llm_docs`) — one
  source of truth for human + model docs.
- The only bespoke editor is the **topology graph** (agents as nodes, delegation/edges as links);
  even node property panels are the schema-driven form.

## Serve-API work (the backend gaps to close first)

The UI is a thin client, so gaps are filled in the serve API (backed by `WorkspaceRuntime`), never in
the UI:

- **Observability reads** — list/stream runs, per-run trace tree, audit events, token/cost. Much is
  in `jobs`/`usage` already; add audit + trace-tree read endpoints (the OTel export gives us the
  data). Powers the monitor views.
- **Authoring flows** — the proposal lifecycle the CLI exposes (`authoring list|show|approve|reject`
  + generate `topology|skill|archetype|mcp-server`). The conversational front door + gap-surfacing
  need these as endpoints.
- **knowledge-pack / validate detail** — already have `validate`; expose knowledge-pack + richer
  validation errors (field paths) so the designer can inline-annotate.

Each new endpoint ships with the contract test pattern the panel uses, so CLI and UI can't drift.

## API shape (illustrative)

```
GET  /api/schema/{artifact_type}         # JSON Schema → drives the designer form + tooltips
GET  /observability/runs                 # monitor: recent runs (+ filters)
GET  /observability/runs/{id}/trace      # per-run span tree (from the OTel bridge)
GET  /observability/audit                # append-only audit events (read-only)
GET  /authoring/proposals                # gap/proposal lifecycle: list
POST /authoring/proposals/{id}/approve   #   approve / reject
POST /authoring/draft                    # conversational: intent → drafted artifact
```

(Existing `/api/topologies|skills|archetypes`, `/run`, `/jobs`, `/conversations`, `/validate`,
`/canary` stand.)

## Sequencing (a program, one design + PR per slice)

1. **This design note** (design-only PR) — posture + scope + the schema-driven-designer decision.
2. **Monitor first** — the "see and monitor" surface: workspace dashboard, runs list, per-run trace
   + cost, audit. Reuses OTel/jobs; lowest risk; delivers the stated primary value early.
3. **Schema-driven form engine** + designer for **skills / archetypes** (simplest schemas).
4. **Topology designer** — the graph editor (agents/edges) with schema-driven node panels. Hardest.
5. **Conversational front door + run/chat consoles** — wire `/authoring/draft` + `conversations`.

## Test plan

- Form engine: property tests that every canonical schema renders (every field has an input +
  tooltip; required/enum/constraint respected) — a golden test over `packages/schema` so a new schema
  field can't ship without a designer control.
- Each serve endpoint: unit + a CLI↔API contract test (no drift), same pattern as the panel.
- E2E (Playwright, local runner — not CI, per `scripts/e2e.sh`): draft an artifact conversationally,
  open it in the designer, edit a field, save, run it, watch the trace appear in the monitor.

## Demo plan

Per slice: monitor slice → a run on the workspace shows up live with its trace + cost. Designer slice
→ create a valid skill entirely in the UI (every field discoverable via tooltips) and `validate` it
green. Topology slice → build a two-agent delegation graph visually and run it.
