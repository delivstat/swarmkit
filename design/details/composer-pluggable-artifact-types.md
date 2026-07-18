# Pluggable artifact types in the composer

**Scope:** UI (`packages/ui` composer) + schema (`packages/schema` UI hints)
**Status:** proposed

Standalone UI capability, not part of the SDLC five. It is the enabling work flagged in
`design/details/pipeline-controller.md` (the stage-graph needs an editor), but it is generically
useful: skills, triggers, stage-graphs, and any future kind all want to be edited in the composer
without a hand-written per-kind editor.

Today the composer special-cases its built-in kinds — the topology canvas, the schema form — with
per-type code. Adding a new artifact kind means writing new UI. This note makes the composer
**schema-driven and pluggable**: a new kind becomes editable by shipping its schema (+ a little UI
intent), not by writing a React editor.

## Goal

Edit **any registered artifact kind** in the composer from its canonical schema — built-in
(topology / skill / archetype / trigger / workspace) and new (stage-graph, future) — with form
editing for free and graph editing via a small per-kind adapter. Adding a kind = add a schema, not a
component.

The unit of auto-generation is not just the editor but the **whole per-kind CRUD surface** — a
schema-driven admin scaffold (the Django-admin / JSON-schema-admin pattern): a **menu entry**, a
**list** screen, a **detail** screen, and **create/edit** forms, all rendered from the schema + a few
`x-swarmkit-ui` hints. Provide a schema, get a full surface; write a screen only for the deliberately
bespoke views (see non-goals).

## Non-goals

- **Not a visual schema designer.** Schemas are authored per `docs/notes/schema-change-discipline.md`;
  the composer *consumes* schemas, it does not let you design them in the UI.
- **Not a new validator or artifact API.** Reuse the canonical schemas + validators and the existing
  artifact save path; generalise them over `kind` where they are currently per-type.
- **Not fully-declarative graph editing.** Form editing generalises cleanly from a schema; graph edge
  semantics do not (see "The graph adapter") — a graph-kind supplies a small adapter, not a bespoke
  editor.
- **Not per-kind bespoke editors.** The whole point is to delete that pattern, including for the
  built-ins (they migrate onto the pluggable path — dogfooding, one code path not two).
- **Not bespoke / cross-kind views.** The scaffold covers *standard per-kind CRUD* only. Purpose-built
  and cross-kind surfaces — the cross-requirement board, the run graph, dashboards, aggregations —
  stay hand-built. Auto-generating those would be the overreach; that is where real design effort goes.

## Where it lives

`packages/ui`: the composer (`composer/page.tsx`), the schema form (`schema-form.tsx`,
`lib/schema-form.ts`), and the graph canvas (`topology-canvas.tsx`) generalise into a registry-driven
renderer. UI intent that the JSON Schema does not carry rides in the schema as a vendor extension
(`x-swarmkit-ui`) in `packages/schema`, so it is single-source and dual-language like the rest of the
schema.

## API shape

### The artifact-type registry

The composer holds an **artifact-type registry** that **auto-populates from the canonical schema
set** — one entry per `kind`. An entry is mostly *derived*:

- `kind`, `schema` — from the canonical schema.
- `title`, `icon`, default `editor` (`form` | `canvas`), widget/reference hints — from the schema's
  `x-swarmkit-ui` block (below).
- `graphAdapter` — only for `editor: canvas` kinds; a small registered function (below).

So adding a kind is: ship its schema (with `x-swarmkit-ui`), and — only if it is graph-shaped —
register one adapter. No new form/editor code.

### Schema-carried UI hints (`x-swarmkit-ui`)

The JSON Schema describes *shape*, not *editing intent* (which field is long-text, which is a
reference to another artifact, whether the kind is graph-shaped). A vendor extension carries that,
embedded in the canonical schema so it travels to both validators and the UI:

```yaml
# schema-level
x-swarmkit-ui:
  title: "Stage graph"
  icon: "workflow"
  editor: canvas            # form (default) | canvas
  list:                     # which fields are list/table columns (else: id + title + first scalars)
    columns: [id, description]
# property-level
properties:
  description:
    type: string
    x-swarmkit-ui: { widget: textarea, listColumn: true }   # textarea in the form; a column in the list
  topology:
    type: string
    x-swarmkit-ui: { widget: ref, refKind: topology }   # dropdown + cross-ref validation; column links to the artifact
```

`widget: ref` is the important one — it turns a free-text id into a validated reference to another
artifact of `refKind`, which powers both the picker UI and cross-reference validation.

### The auto-scaffolded CRUD surface

From one registry entry, the composer renders the full per-kind surface — no per-kind screens:

- **Menu / nav entry** — one per registered kind (`title` + `icon` from `x-swarmkit-ui`).
- **List screen** — a table over that kind's artifacts. Columns come from `x-swarmkit-ui.list.columns`
  (or the default: `id` + title + the first scalar fields); `ref` columns link to the referenced
  artifact; search/sort/filter on scalar columns is generic; `gated` kinds show status/version. Rows
  open the detail screen.
- **Detail screen** — the artifact rendered read-only: the form in read mode, or the read-only canvas
  for `editor: canvas` kinds. Shows version history (diff/rollback) because it is a registered artifact.
- **Create** — the editor seeded with schema defaults + required fields; save creates a new artifact.
- **Edit / Delete** — the editor (below) and a governed delete; both respect the kind's `gated` flag.

All of it is derived: list, detail, and create reuse the same schema + hints + editor that edit does,
so a new kind gets a working admin surface with no bespoke screens.

### Editor resolution (form vs canvas)

The composer looks up the kind → picks the editor:

- **`form` (default, fully generic):** `schema-form.tsx` renders the whole artifact from the schema +
  `x-swarmkit-ui` widgets. Any kind with a schema gets a usable editor for free — the 90% case.
- **`canvas`:** the graph canvas renders via the kind's graph adapter.

The built-in topology migrates to `editor: canvas` + a graph adapter, so its canvas is no longer a
special case but the first consumer of the general mechanism.

### The graph adapter (for graph-shaped kinds)

Graph editing does **not** fully generalise, honestly: a topology's edges are its `children` tree,
while a stage-graph's edges are event-wired (`success → on`) plus explicit `loops`. Edge semantics are
domain-specific, so a graph-kind registers a small **adapter** (a pure function set, mirroring the
`traceToGraph` pattern already in `control-plane-ui/lib/trace-graph.ts`):

```ts
interface GraphAdapter<T> {
  toGraph(artifact: T): { nodes: Node[]; edges: Edge[] }   // extract nodes + edges
  onLayout(artifact: T, positions): T                       // persist node positions
  onConnect(artifact: T, from, to): T                       // an edge drawn → mutate the artifact
  onAdd(artifact: T, kind): T; onRemove(artifact: T, id): T
}
```

This is a small, testable interface — a function set, not a React editor. The canvas component,
node-drag, palette, and save are shared; only the adapter is per-kind.

### Cross-reference validation

With the workspace in context, `widget: ref` fields validate that the referenced artifact exists
(the named `topology`/`gate`/`skill` is present) — the checks a schema alone cannot make. Dangling
references surface inline in the editor, before save. This is why editing belongs *in the composer*
(which has the workspace) and not in a standalone tool.

### CRUD + save path (kind-generic)

The scaffold needs the artifact API to be **kind-generic**: `list / get / create / update / delete` by
`kind` (generalising the endpoints that are currently per-type — the one real backend piece). The
composer's list/detail/create/edit screens are thin clients over those.

Saves go through the existing staged-edit → Save flow (the composer already stages edits and PUTs on
Save). A kind may declare itself **gated** (`x-swarmkit-ui: { gated: true }`) — a change to it is a
reserved-scope act (e.g. a stage-graph edit is `topologies:modify`-class); the composer then routes
create/edit/delete to the growth-loop **proposal → approval** path instead of a direct write, and
shows it as pending. Versioning, diff, and rollback come for free because it is a registered artifact.

## Test plan

- **CRUD scaffold pluggability:** a brand-new kind (only a schema + `x-swarmkit-ui`) yields a menu
  entry, a list screen (columns from hints / default heuristic), a read-only detail screen, and a
  create/edit form — with **no new React**, asserted by registering a fixture kind in a test.
- **List columns:** `list.columns` / `listColumn` drive the table; a `ref` column links to its target;
  scalar columns sort/filter.
- **Widgets from hints:** `widget: textarea` renders a textarea; `widget: ref` renders a picker
  populated from the workspace's artifacts of `refKind`.
- **Graph adapter:** a graph-kind's `toGraph` extracts the expected nodes/edges; `onConnect`/`onAdd`/
  `onRemove` mutate the artifact correctly; node-drag persists via `onLayout`.
- **Built-in migration:** topology edits identically after moving onto the pluggable path (no
  regression — same nodes/edges/save).
- **Cross-ref validation:** a `ref` to a missing artifact flags inline; to an existing one passes.
- **Gated kind:** a `gated` kind routes to a proposal (not a direct PUT) and renders as pending.

## Demo plan

`just demo-composer-pluggable` (or a recorded walkthrough): register the `StageGraph` kind by schema
+ `x-swarmkit-ui` + a graph adapter, and edit a stage-graph in the composer — form fields for stage
detail, canvas for the stage/transition graph, a `ref` picker for each stage's topology, a dangling
ref caught inline, and Save routed to a proposal. No new editor component written. Screenshot/GIF in
the PR body.

## Schema-change checklist

Adds the `x-swarmkit-ui` vendor extension to the canonical schemas — follow
`docs/notes/schema-change-discipline.md`: the extension is ignored by validators (vendor keyword) but
documented in the schema contract, and the Python + TS schema packages ship it identically. New kinds
(e.g. `StageGraph`) still follow the normal schema-addition path; this note only adds how the composer
*renders* them.
