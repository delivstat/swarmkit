# A stage graph routed by events, not by position

**Status:** proposed — design only.

## Goal

Let a routing stage choose which of several declared paths a run takes, without inventing anything
at runtime: a triage stage emits `ticket.dev` or `ticket.design` or `incident.declared`, and the
graph goes where it says.

## Where it stands

Three facts, in rising order of how much they constrain the idea.

**A stage can be entered by many events and can leave by exactly one.** `when` is an array of
events; `success` is a single `$ref: event`.

**A stage run cannot express a choice.** `StageOutcome` carries `status`, `artifact`, `detail`,
`artifact_bytes`. There is no field an agent's decision could travel in.

**The bundled controller does not route on events at all.** `_drive` computes
`index = len(saga.passed_stages)` and takes `stages[index]` — it advances by **position in the
array**. `_entry_events` is used in exactly one place, to decide which *graph* an inbound external
event starts. Mid-run, the declared event graph is interpreted as a list; the drop path says so:
*"the bundled controller advances sequentially / via gates, so it does not re-route mid-run"*.

So `success:` is currently closer to documentation than to wiring, and the ceiling is one layer
below where it looks.

## The shape

The set of paths stays **declared**. Only the selection becomes dynamic.

```yaml
- id: triage
  topology: wms-triage
  when: [ticket.created]
  emits: [ticket.dev, ticket.design, incident.declared]   # the declared choice set
  gate: triage-review

- id: build
  topology: wms-build
  when: [ticket.dev]

- id: design
  topology: wms-design
  when: [ticket.design]
```

Every possible route is in the file: inspectable, diffable, gate-coverable, and checkable by the
reachability report. Nothing is created during the run.

This is deliberately weaker than letting an agent propose new stages. That version buys more power
and forfeits replayability, inspectability and static checking — the properties this codebase spent
a week repairing. A choice from a declared set gives most of the flexibility and gives up none of
them.

### How the agent's choice travels

Not parsed out of prose. The routing stage's topology declares an `output_schema` whose reserved
`next_event` field is an enum of the stage's `emits`:

```json
{"type": "object", "required": ["next_event"],
 "properties": {"next_event": {"enum": ["ticket.dev", "ticket.design", "incident.declared"]}}}
```

Structured-output governance already validates this deterministically and auto-corrects a
field-specific failure, so a model that emits anything outside the set is corrected before anything
routes. This is the existing "LLM language, code doing" split: the model produces a label, code
decides what the label means.

The runtime derives the enum from `emits` rather than trusting the workspace to keep two lists in
step — a mismatch between them is the exact drift this codebase keeps finding.

## The part that is real work

Everything above is small and additive. The controller is not.

`passed_stages` is currently doing two jobs: the visited history *and* the position pointer. Event
routing kills the second, and with it the completion condition (`index >= len(stages)`), the
absorption guard in `_reconcile` (`sid in saga.passed_stages`, which a revisited stage breaks), and
the assumption that a stage runs at most once.

Proposed saga state: keep `passed_stages` as ordered history, add `visits: dict[str, int]`, and stop
deriving control from either. That is a state-shape change with a migration.

### Termination

With a list, "done" is the end of the array. With a graph it has to be decided:

**Terminal is derived: a stage whose emitted event no stage consumes ends the run.** The codebase
already models this — `gate_coverage` computes `terminal` as "no downstream stage consumes this
stage's `success`". Reusing it keeps one definition.

The risk is that "intentionally terminal" and "misconfigured" look identical at runtime. That is
what makes the static check load-bearing rather than nice-to-have: **a declared event no stage
consumes must be reported by `swarmkit validate`**, so at runtime a missing consumer can be treated
as deliberate.

### Cycles

Branching makes cycles natural (the defect loop already wants one). Bound them: a per-stage visit
cap, defaulting to a small number, that parks the saga with a stated reason rather than looping.
`attempts` already exists per stage and is the obvious place; `visits` is the new sibling because a
retry and a revisit are different facts.

### Fan-out is out of scope

If two stages declare `when: [ticket.dev]`, v1 treats it as a **validation error**, not as parallel
branches. The saga has one `current_stage`; concurrent branches need a fundamentally different state
shape (and a different answer for gates, staleness and reconciliation). Naming it as excluded is
cheaper than discovering it half-built.

### Backwards compatibility

Every existing graph is a sequence with no `emits`. Proposed rule: **route by event when the
candidate next stage declares `when`; otherwise fall through to the next stage in declaration
order.** Existing graphs behave byte-identically, and a graph opts in per stage.

This is the part I am least sure of — see the open questions.

## Non-goals

- Not a work graph discovered at runtime. Stages are declared; only the path is chosen.
- Not parallel branches.
- Not a change to how a stage runs, what a funnel does, or how gates park.
- Not a replacement for `success:`, which stays as the single-outcome shorthand.

## Second-order effects

**`gate_coverage` assumes linear edges.** "The narrowest verified edge" means something else when a
stage has three outgoing edges with different gates downstream — probably "narrowest reachable
path". That analysis needs revisiting, and it is user-visible (`swarmkit gates --require` gates CI).

**Reachability gains a natural new finding.** An event declared in `emits` that no stage consumes,
and a `when` no stage emits, are both *declared-but-unreachable* — same shape, same report, same
`--require`. A branch that can never be taken is exactly what should fail at `validate` rather than
in production.

**Saga staleness and reconciliation** both read `current_stage` and `passed_stages`; both need
re-reading against the new control state, and the gated-stage refusal path (1.171.0/1.174.0) has to
keep working when a stage can be revisited.

## Test plan

- A router emitting each of its declared events routes to the matching stage, and only that stage.
- An event outside `emits` is rejected by output validation and corrected, never routed on.
- A stage with no matching consumer terminates the run cleanly, and `validate` reports the dangling
  event before that can happen by accident.
- A cycle is bounded: the visit cap parks with a stated reason rather than looping.
- Two consumers of one event is a resolution error, named at the graph.
- **Every existing sequential graph produces an identical saga timeline** — the compatibility rule
  asserted against the reference workspaces, not just a fixture.
- Reconciliation, the gated-stage refusal, and operator release still behave as in 1.174.0 when a
  stage is revisited.
- Migration: a saga persisted under the old shape resumes.

## Demo plan

`examples/` gets a triage graph with three branches: a ticket routed to build, the same graph
routing a different ticket to design, and one declaring an incident — same file, three timelines,
shown as `swarmkit pipeline status` output. Plus `swarmkit validate` reporting a deliberately
dangling branch.

## Open questions for review

1. **The compatibility rule.** "Route by event when the next stage declares `when`, else fall
   through" keeps every existing graph working, but it means one graph can be half-sequential and
   half-routed, which may be harder to reason about than a per-graph `routing: events` switch.
   Weak preference for the fall-through rule because it needs no migration of existing files.
2. **`next_event` as the reserved key.** Explicit and unlikely to collide, but it puts a runtime
   concept inside a workspace's artifact schema. The alternative is a separate channel on
   `StageOutcome` populated by the stage runner from somewhere other than the artifact.
3. **Should `emits` replace `success` eventually,** with `success: x` sugar for `emits: [x]`? One
   concept is better than two, but deprecating a field in every existing graph is the kind of churn
   this note is otherwise trying to avoid.
4. **Does the visit cap belong on the stage or the graph?** Per-stage is more precise; per-graph is
   one number an operator can reason about.
