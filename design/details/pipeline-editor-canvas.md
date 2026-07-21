---
title: Pipeline editor canvas
description: A visual editor for the StageGraph artifact — drop topologies as stages, wire them with event-based connections (signal / external / loop), configure per-stage gates, locks, and compensation, and save through the approval path.
tags: [ui, composer, pipeline, stage-graph, authoring]
status: proposed
---

# Pipeline editor canvas

**Scope:** UI (composer) — a new editing surface for the `StageGraph` artifact.
**Design references:** [`pipeline-controller.md`](pipeline-controller.md) (the StageGraph + its
saga execution), [`composer-pluggable-artifact-types.md`](composer-pluggable-artifact-types.md)
(the composer's per-kind editor model), [`stage-graph` schema](../../packages/schema/schemas/stage-graph.schema.json).
**Status:** proposed — design only. What ships today is the read-only pipeline canvas + the
generic schema-form + YAML; this note designs the *editing* canvas that supersedes hand-authoring.

## Why

A `StageGraph` (a "pipeline") is your delivery process as data: the ordered stages a requirement
travels through, each delegating to a topology, wired by events, gated and locked. It is a genuine
authored **DAG** — arbitrary stages, arbitrary wiring, loops — and hand-writing it in YAML is
error-prone: you manage stage ids, keep `success` signals and `when` events in sync by hand, and
have no picture of reachability, fan-out, or the defect loop.

Unlike the **Funnel** (a *fixed* pipeline whose shape must not be user-rewired — see
`gate-funnel.md`), a StageGraph is exactly the kind of thing a canvas should let you draw. The
`pipeline-controller.md` design already calls for it: *"authored/edited in the composer — form +
canvas, ref-validated, routed through the growth-loop proposal → approval path."* This note makes
that concrete.

### The one idea that makes the editor non-trivial

**Connections in a pipeline are events, not pointers.** An arrow from `design` to `build` does not
store "build follows design." It means: *`design` emits the signal `design.approved`, and `build`
listens for `design.approved` in its `when`.* The edge is **derived** from a shared event name.
Some entries have no source stage at all (a CI/Jira **webhook**), and some edges run *backwards*
(a defect **loop**). A naive "draw an arrow between two boxes" tool would produce a picture that
does not match the YAML it saves. The editor's core job is to let you draw arrows while keeping the
event model — and the YAML — authoritative and honest.

## Goal

A canvas mode in the composer's pipeline editor where a user can:

1. Drop workspace **topologies** onto the canvas as **stages**.
2. Wire stages with the three **connection types** (signal, external-entry, loop), each of which
   writes the correct `when`/`success`/`loops` YAML.
3. Configure each stage's **gate**, **locks**, `release_locks_on`, and **compensation** in a node
   inspector, using workspace-populated pickers.
4. See **live validation** (unknown refs, unreachable stages, dangling signals) on the canvas.
5. **Save** through the approval path (a proposed change, diffed and human-approved — editing a
   pipeline is a `topologies:modify`-class act), never a silent write.

## Non-goals

- **Not a new execution model.** The canvas edits the same `StageGraph` artifact the controller
  already executes; it changes *authoring*, not runtime or controller behaviour.
- **Not AND-joins in v1.** A stage's `when` is an **OR** — it starts when *any* listed event
  arrives. "Wait for build AND security-scan" (a synchronizing join) is out of scope; see open
  questions.
- **Not a free-form diagram tool.** Every node is a real stage bound to a real topology; every edge
  is a real event relationship. The canvas cannot draw something the schema can't represent.
- **Not replacing form/YAML.** Canvas is the primary surface for *structure + wiring*; the form and
  YAML modes remain for fields the canvas doesn't visualize and for power edits. All three are views
  of one authoritative document.

## Domain model (the entities)

### Stage

The unit on the canvas. **A stage is an instance in this pipeline; it is not the same as a
topology.** Two stages can bind the same topology (e.g. `sit` and `re-test` both run `oms-sit`), so
a stage has its own `id`, distinct from the topology it references.

| Field | Meaning | Editor surface |
| --- | --- | --- |
| `id` | unique stage id within the graph | node title (editable, uniqueness-checked) |
| `topology` | the swarm this stage runs (required, ref) | set on drop; changeable via a topology picker |
| `when[]` | entry events that start the stage (OR) | **incoming** connections + external-entry pins |
| `success` | the signal emitted on clean completion | **outgoing** signal-edge label |
| `gate` | a Funnel the run parks on (ref) | node inspector → funnel picker |
| `locks[]` | integration-contract ids held during the run | node inspector → lock chips |
| `release_locks_on` | the signal that releases this stage's locks | node inspector → event dropdown |
| `compensation` | topology run in reverse on cancel (ref) | node inspector → topology picker |

Visual: a card showing the stage id + bound topology, with badges for gate / lock-count /
compensation, and an "entry" badge when it has no incoming stage edge.

### Topology (palette unit)

A reusable swarm definition (a `kind: Topology` artifact). The editor's **palette** lists the
workspace's topologies; dropping one **creates a stage** bound to it. A topology may appear as many
stages. The palette also lists **Funnels** (for the gate picker) and re-uses topologies for the
compensation picker. Nothing about a topology's internals is edited here — only *which* topology a
stage runs.

### Event / signal (the wiring substrate)

A plain string name (e.g. `design.approved`, `build.ready-in-qa`, `defect.raised`). Every event
plays one or both of two roles:

- **Emitted** — a stage's `success` produces it.
- **Listened-for** — it appears in a stage's `when` (or a `loops[].when`).

An event is **internal** if some stage emits it as `success`, and **external** if it appears in a
`when` but no stage emits it (a webhook from CI / Jira / Git / SAST). Internal-vs-external is what
distinguishes a normal edge from an inbound pin, and today it is *inferred*; §"A schema evolution"
proposes making it explicit.

### Connection — the three types

Connections are the heart of the editor. Only **loops** are stored explicitly (in `loops[]`); the
other two are **derived** from event-name matching. The editor presents all three as edges but
writes them differently.

#### 1. Signal edge (forward flow)

- **Represents:** "when stage A completes cleanly, stage B starts." The normal pipeline sequence.
- **Data:** `A.success = S` and `S ∈ B.when` for a shared signal `S`.
- **Draw gesture:** drag A's output handle to B's input.
  - If A has no `success`, the editor assigns one (default `<A.id>.done`, editable) and adds it to
    `B.when`. If A already emits `S`, it just adds `S` to `B.when`.
- **Fan-out:** one `success` in several stages' `when` = those stages start in **parallel**. Drawing
  A→B and A→C reuses A's single `success` signal.
- **Fan-in (OR):** a stage with several `when` events starts on **any** of them (not all).
- **Visual:** solid arrow, labelled with the signal name.

#### 2. External-entry connection (inbound webhook)

- **Represents:** "an outside system triggers this stage" — `requirement.created` from intake,
  `build.ready-in-qa` from CI. It has **no source stage**.
- **Data:** an event in `B.when` that no stage emits.
- **Draw gesture:** add an **external-entry pin** to a stage and name its event; it renders as a
  distinct inbound source (a pill/pin), not a stage-to-stage arrow.
- **Visual:** a labelled inbound pin (dashed, "external") anchored to the stage — deliberately not an
  arrow between two boxes, because there is no source box.

#### 3. Loop edge (defect re-entry)

- **Represents:** "on this event, jump *back* to an earlier stage" — `defect.raised → design`. The
  cross-stage/defect cycle.
- **Data:** a `loops[]` entry `{ when: E, to: <stage-id> }`. Stored explicitly (unlike the other
  two) because its target is an intentional back-edge, not a forward signal match.
- **Draw gesture:** draw an edge and mark it a **loop** (or draw to an already-upstream stage, which
  the editor recognizes as a back-edge and offers to make a loop); pick the trigger event `E`
  (internal signal or external).
- **Visual:** dashed/animated back-edge, labelled with the trigger event.

> **Why this asymmetry (derived vs explicit) is deliberate:** forward edges *emerge* from the normal
> `success → when` flow, so storing them separately would duplicate state that can drift. Loops are
> an author's explicit decision to re-enter, and the target is not implied by any signal the source
> emits — so they are first-class data. The editor hides the asymmetry (both look like edges) but the
> save writes each to its honest home.

### Per-stage configuration

Not connections — **node properties**, edited in the inspector:

- **Gate** (`gate`) — a Funnel picker (workspace funnels). Sets the stage's quality gate; the run
  parks on it (design/details/gate-funnel.md).
- **Locks** (`locks[]`) — contract-id chips (free strings today; a shared contract registry is a
  future refinement). Held before the run, released per `release_locks_on`.
- **Release-on** (`release_locks_on`) — a dropdown of events known to the graph (so you release a
  contract on, e.g., `design.approved`).
- **Compensation** (`compensation`) — a topology picker; the unwind run on cancellation.

Locks also drive a **canvas overlay** (not a connection): stages contending on the same contract id
can be highlighted together, so a user sees serialization points at a glance.

### The graph as a whole

- **Entry stages** — a stage with only external-entry events (indegree 0 among stage edges). The
  pipeline's start point(s).
- **Terminal stage** — a stage whose `success` no stage listens for (and which isn't external). Fine
  — it's the end. The editor marks it, and does *not* warn about its unmatched signal.
- **Reachability** — every non-entry stage should be reachable via forward edges from an entry; the
  editor warns on orphans.

## Canvas interactions → artifact mutations

Every gesture is a pure, inspectable mutation of the `StageGraph` document. The YAML is
authoritative; the canvas is a projection that also *writes*.

| Gesture | Mutation |
| --- | --- |
| Drop topology `T` | append stage `{ id: <unique-from-T>, topology: T }` |
| Rename stage | set `stage.id` (+ rewrite any `loops[].to` that referenced it) |
| Change a stage's topology | set `stage.topology` |
| Draw signal edge A→B | ensure `A.success = S` (default `<A.id>.done`); add `S` to `B.when` |
| Delete signal edge A→B | remove `S` from `B.when`; clear `A.success` if now unmatched |
| Add external entry to B | add event `E` to `B.when` (+ mark external — see §schema evolution) |
| Draw loop A→B (trigger `E`) | append `loops: { when: E, to: B }` |
| Delete loop | remove that `loops[]` entry |
| Set gate / compensation | set `stage.gate` / `stage.compensation` |
| Add/remove lock | edit `stage.locks[]` |
| Set release-on | set `stage.release_locks_on` |
| Edit metadata/provenance | a graph-details panel (not on the canvas) |

## Projection & round-trip

- **YAML is the source of truth.** The canvas is generated from the document and every edit produces
  a new document; form / YAML / canvas are three views of one artifact and stay in lockstep.
- **Derivation** reuses the shipped read-only projection (`packages/ui/lib/stage-graph.ts`): forward
  edges from `success → when` matches, loop edges from `loops[]`, and external entries from `when`
  events no stage emits.
- **Fidelity:** a canvas edit must never drop fields it doesn't visualize (metadata, provenance,
  future stage fields). Edits go through a structured model that preserves unknown keys; YAML
  comments are the one thing a structural round-trip can lose (open question).

## Validation

The editor surfaces two tiers, both anchored to the offending node/edge:

1. **Structural (schema + resolver ref-check, already built):** `topology` / `compensation` exist,
   `gate` is a known Funnel, stage ids unique, `loops[].to` names a real stage. These are the
   `stage-graph.*` resolution errors; the canvas renders them inline instead of a bare error list.
2. **Semantic (editor affordances):** unreachable stage (no entry path); a `when` event that is
   neither an internal signal nor a declared external event (probable typo vs. a real webhook);
   `release_locks_on` referencing an event never emitted; a `success` that is unmatched but the stage
   is not terminal (dangling signal). These are *warnings*, not save-blockers.

## Save & governance

Editing the pipeline is a **`topologies:modify`-class act** (design §8.7): it changes how work is
sequenced and gated across weeks. So **save is not a silent write** — it produces a **proposed
change**, diffed against the published version, and routed through the growth-loop
proposal → approval path. The controller reads only the **published** version, so an in-progress
canvas edit never perturbs an in-flight requirement. (This mirrors `pipeline-controller.md` "Where it
lives".)

## Relationship to what already exists

| Piece | Role for the editor |
| --- | --- |
| `stage-graph.schema.json` | the contract the canvas reads/writes; the source of field truth |
| resolver ref-check (`_stage_graphs.py`) | structural validation rendered inline |
| read-only canvas (`stage-graph.ts` + `stage-graph-canvas.tsx`) | the projection + rendering the editor extends with gestures |
| composer schema-form + YAML modes | the sibling views; the fallback for non-visual fields |
| serve CRUD (`/pipelines`, `/api/pipelines/{id}`) | load/save transport (save becomes propose-not-write) |
| the controller | the consumer that executes the *published* result — unchanged by this feature |

## A schema evolution this motivates (proposed, not decided)

Today external-vs-internal is **inferred** (a `when` event no stage emits ⇒ external), and the
controller takes `external_events` as *separate config*. That inference is fine for rendering but
weak for authoring: the editor can't tell a real webhook from a mistyped signal, and the controller's
notion of "external" lives outside the artifact. Proposal: let a stage declare its inbound external
events explicitly (e.g. a per-`when` marker or a graph-level `external_events: [...]`), so external
entries are **data** — ref-checkable, editor-distinguishable, and controller-consumable from the one
artifact. This is a small, backward-compatible schema addition; flagged here as the editor's main
pull on the schema, to be decided before build.

## Open questions

1. **Explicit external events** — adopt the schema addition above, or keep inference? (Leaning
   explicit — it removes an ambiguity the editor otherwise has to guess.)
2. **AND-join / synchronization** — v1 `when` is OR. Do we need "start when build *and* scan both
   signalled"? If yes, it's a schema + controller change, not just an editor one.
3. **Signal naming** — auto-name `<stage>.done` vs. always prompt. Auto with inline-edit is the
   proposed default.
4. **Comment preservation** — accept that the structural round-trip drops YAML comments, or invest in
   a comment-preserving loader?
5. **Contract-lock registry** — locks are free strings now; do we want a first-class contract
   artifact so lock ids are picked, not typed (and the contention overlay is exact)?

## Test plan

- **Projection round-trip:** document → canvas model → document is identity for every fixture
  (including loops, external entries, fan-out, shared-topology stages).
- **Gesture mutations (unit):** each gesture in the table produces exactly the specified YAML change;
  deleting the last consumer of a `success` clears it; renaming a stage rewrites `loops[].to`.
- **Validation (unit):** unreachable stage, dangling signal, unknown ref, and `release_locks_on` on a
  never-emitted event each surface on the right node/edge.
- **Save flow (integration):** a canvas edit produces a proposed change (not an immediate write); the
  published version the controller reads is unchanged until approval.
- **Fidelity:** an edit to a canvas-visualized field preserves untouched metadata/provenance/unknown
  fields.

## Demo plan

Build the `oms-pipeline` from an empty canvas: drop `oms-intake` → `oms-design` → `oms-build` →
`oms-sit`; wire the forward signals; add the `requirement.created` and `build.ready-in-qa` external
entries; set `oms-design`'s gate to `oms-design-gate`, its `locks` + `release_locks_on`, and its
compensation; draw the `defect.raised → oms-design` loop; watch live validation clear; save as a
proposed change and show the diff. Result: the exact `pipelines/oms-pipeline.yaml` the controller
already runs — authored without touching YAML. Screen recording in the PR.
