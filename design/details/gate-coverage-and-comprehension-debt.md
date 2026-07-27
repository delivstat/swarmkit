---
title: Gate coverage & comprehension-debt signals
description: Make the narrowest verified edge visible, and treat human comprehension as a designed discipline (plan-first objectives, slice budgets, cited change descriptions, recurring expert audits) rather than a gate you can fake.
tags: [governance, funnel, stage-graph, observability, review]
status: draft
---

# Gate coverage & comprehension-debt signals

**Scope:** `governance`, `topology`, `observability` — read-side analysis + telemetry over the existing gate/funnel/audit surface, plus one reference audit topology. Mostly additive; no change to the execution model.
**Design reference:** builds on `gate-funnel.md`, `pipeline-controller.md`, `human-interaction-model.md`, `multi-party-approval.md`, `decision-skills.md`. §8 (Separation of Powers) is the governing frame.
**Status:** draft (design-only).

## Goal

Give a SwarmKit graph two things it currently lacks: **(A)** a view that names its *narrowest verified edge* — every place work crosses a boundary with no gate, or only a weak one — and **(B)** signals that flag when the human gates are being **rubber-stamped rather than understood**. Around both, formalize the practices that actually build comprehension so they are declarable, checkable, and schedulable instead of tribal.

## Framing (why this note exists)

The graph-engineering discourse landed on one durable law (Osmani): *a graph's throughput is set by its narrowest verified edge, not its widest generating node.* The failure mode the telemetry keeps showing — incidents up ~240%, no-review merges up a third, agent-built codebases "struggling after three to six months" — is **comprehension debt**: the widening gap between how much code exists and how much any human still understands. It accrues at exactly the edges SwarmKit already models, and SwarmKit already owns the right primitives (funnels, reserved human scopes, the audit log). What it does **not** have is (1) a way to *see* where the gates are weak, and (2) any pressure against a human approving faster than they could possibly have read.

**The honest constraint, stated up front:** *you cannot gate comprehension.* No automated check verifies that a human understood a change; a `decision` skill that claims to is a lie that will be optimized against. So this note deliberately splits into what is measurable (gate *presence/strength*, approval *behavior*) and what is only *cultivatable* (the practices that produce understanding). We gate and measure the leading indicators; we make the practices first-class; we never pretend to gate the thing itself.

## Applicability & default posture

**Opt-in and additive.** Nothing here changes an existing workspace — absence = prior behavior (the same rule as the `executor` block: no config ⇒ unchanged). Adoption is per-field and per-gate: declare `objective`/`slice_budget`, bind `plan-objective`, add a `cited-change` funnel, author the audit trigger. A topology that adopts none of it runs byte-identically.

**Split default — visibility on, enforcement off.** The *reports* (gate coverage, comprehension signals) are always available and shown passively in the composer/panel at zero cost; they never block. *Enforcement* — a CI `--require` floor, or a signal promoted to a decision-gate threshold — is always an explicit opt-in. Surface the question without imposing the answer.

**Scoped to the delivery-pipeline shape — by structure, not a flag.** These features attach to `StageGraph` + `Funnel` + human approval, so they only appear where those primitives are used:

- **light up** for long-lived, multi-stage, durable-artifact-producing, human-gated, repeated work (the software-factory shape);
- **partially apply** to any human-gated multi-agent topology — gate coverage and the fast-approve / unreviewed-merge signals need only an approval gate;
- **do not apply** to single-shot answer swarms (no edges/gates), read-only analysis / research swarms (nothing durable accrues, so no comprehension debt), or interactive chat.

Comprehension debt is specific to durable-artifact work; SwarmKit is broader (analysis, curation, chat), so this is deliberately a pipeline feature, not a framework-wide one. The scoping is **structural** — a workspace that never uses a StageGraph or a human funnel simply never reaches it, with no flag to set.

## Surface parity — CLI ⇄ serve UI (cross-cutting)

Everything in this note obeys SwarmKit's **thin-interface rule**: business logic lives in the `WorkspaceRuntime` service layer, and the CLI and the serve web app are **both thin clients** over it (`cli-architecture`). This note therefore ships **no CLI-only capability** — every command below is a service-layer method surfaced twice.

- **Every read is a serve endpoint *and* a UI view**, not just a CLI table. `swarmkit gates` ⇄ `GET …/gate-coverage` ⇄ the pipeline-canvas overlay; `swarmkit comprehension` ⇄ `GET …/comprehension` ⇄ a comprehension panel in the serve web app. The **fleet panel** is the *cross-instance* projection of the same endpoints; the **single-instance serve UI** is the per-workspace one — both exist, neither is the only one.
- **Every gate *action* is available in the serve UI**, never terminal-only: resolving a human approval (approve / reject with the last critique attached), answering a relayed permission or input request, and viewing live multi-party quorum state — all through the existing `/review` surface, extended to the funnel and multi-party gates.
- **Every gate *configuration* is editable in the serve UI**: the funnel layers, the approval policy (roles / quorum / `min_distinct_approvers` / `exclude_author`), the role registry, and the new stage fields (`objective`, `acceptance`, `slice_budget`) are all **artifact data the composer/canvas already edits** — the same schema-form surface, not a second config path.

This is not added scope; it is the constraint the rest of the note is written against. Any endpoint or config named below is the service-layer capability surfaced in both places.

## Gate composition — arrays live in decision skills; a funnel is a hard human gate

Two gate mechanisms, deliberately different cardinality — and this note adds nothing to the funnel's shape:

- **Composition is array-shaped and lives in `decision_skills[]`** (bound at `pre_input` / `post_output` / `checkpoint` / `pre_synthesis`) **and the automated gate layers.** Any number of agent-specific or heavyweight automated checks attach here — this is the unbounded extension point. `plan-objective` (Part C) is one such binding; a domain compliance check, a naming rule, or a security pre-check are others.
- **A `Funnel`'s output is a *hard human gate* — only.** Exactly one human exit; never diluted into another automated check, never stacked (`funnel:` / stage `gate:` stay a **single ref**, never an array), never grown into arrays of automated layers. The funnel's built-in `validate` / `judge` / `review` stay **fixed, single-slot pre-filters** — a convenience in front of the human approval, not a composition surface. When you need more automated checking, you add a decision skill; you do not reshape the funnel.

Consequence for this note's three new gates: `plan-objective` is a `decision_skills[]` binding, `slice_budget` is a routing property on the stage, and `cited-change` is a funnel on a *separate* artifact (the change-rationale). None of them touch, stack, or grow the code output's one human gate — the funnel means exactly one thing everywhere it appears.

## Part A — Gate coverage (the narrowest verified edge)

Because topology **is data**, the gates are already declared — we simply never *report* on them. A pure static pass over a `StageGraph` + the `Funnel`s its stages reference (and over a `Topology`'s per-node funnels) classifies every edge by the **strongest gate layer** present, and surfaces the weakest.

### Gate taxonomy (strongest layer wins)

Ordered weak → strong, mirroring the funnel layers (`gate-funnel.md`):

| Class | Source | What it verifies |
|---|---|---|
| `passthrough` | no `gate:` on the stage / no `funnel:` on the node | **nothing** — work crosses the edge unverified |
| `deterministic` | funnel `validate` only | output *shape* (schema + auto-correct); not correctness |
| `judged` | funnel `judge` | an LLM rubric score above `threshold` |
| `reviewed` | funnel `review` | a harness reviewer's findings (advisory, routes back at `route_back_at`) |
| `human` | funnel `approve` | a real human approval (multi-party, reserved scope) |

A stage-graph *transition* is `passthrough` when the source stage has no `gate:`; an external event edge (CI/SAST) is annotated separately because its "gate" lives outside SwarmKit.

### Output

- **CLI:** `swarmkit gates <workspace> [pipeline|topology]` — a table of edges with their gate class, plus a one-line **verdict**: *"narrowest verified edge: `build → sit` is `passthrough`."* Exit non-zero (CI-gatable) if any edge on a `--require deterministic|human` floor is below it. This is analogous to `swarmkit eval` gating CI on quality — here it gates CI on *gate presence*.
- **Canvas overlay:** color every edge on the pipeline-editor canvas by gate class; `passthrough` edges render red. The topology-as-data payoff — the coverage map is just the topology, re-projected.
- **Serve endpoint + UI:** `GET /api/pipelines/{id}/gate-coverage` — the **same data behind the CLI table**, rendered as the canvas overlay in the single-instance serve web app and aggregated in the fleet panel, so an operator sees the weakest edge per running pipeline in either surface (per **Surface parity**).

Entirely static / read-only. No execution, no new runtime state, no schema change.

## Part B — Comprehension-debt telemetry

Everything here is **derived from the existing audit log** (`human-interaction-model.md`) — no new capture path, opt-in reporting, never a silent block. These are *signals*, surfaced **identically across every surface** (per **Surface parity**): the `swarmkit comprehension <workspace>` CLI, a `GET /comprehension` serve endpoint feeding a comprehension panel in the single-instance serve web app, and the fleet panel for the cross-instance roll-up — the same service-layer computation behind all three. A workspace may *optionally* promote any signal to a decision-gate threshold, but the default is report-only.

| Signal | Derived from | What it suggests |
|---|---|---|
| **fast-approve** | approval latency vs. an estimated read-time for the artifact size | a human approved faster than they could read it — a rubber stamp |
| **oversized-slice** | artifact/diff size above a budget that reached a human gate without a `review` layer | the "2000 lines at the end" anti-pattern; should have been sliced |
| **unreviewed-merge** | an edge crossed at `human` floor with the approval auto-satisfied / quorum trivially met | the "no-review merge" the telemetry counts |
| **uncited-change** | a change-rationale artifact (Part C) that cites no code locations | a description that doesn't demonstrate understanding |
| **stale-audit** | wall-clock since the last recurring expert audit (Part C) exceeded its cadence | accumulated debt is going unexamined |

**Deliberately not a score.** There is no single "comprehension number" — that invites gaming and false comfort. The output is a list of edges/approvals with the signal that fired, most-recent first, so a human decides. (Same stance as the funnel's "automated layers filter but never decide.")

## Part C — Comprehension as a designed discipline

Telemetry catches erosion after the fact. What actually *prevents* it — from real practice on this repo — is upstream, and SwarmKit can make each practice a first-class, declarable thing. **Ordered by leverage** (an hour on an upstream edge collapses days downstream):

### 1. Plan-first, with a clear objective per milestone (highest leverage)

The single biggest lever is a plan made **before** the work, with an explicit objective and an acceptance check for each milestone. SwarmKit already has the shape: a `StageGraph`'s stages *are* milestones, and `design/IMPLEMENTATION-PLAN.md` is the repo's own worked example. Formalize it:

- A stage may declare `objective:` (what "done" means) and `acceptance:` (the check that proves it) — carried as data on the stage.
- A lightweight `plan-objective` **decision gate** (reference skill) runs *before* a stage's implementation starts and refuses to proceed if the stage has no stated objective + acceptance. This is the "agree on the plan before building" edge, made structural. It gates the *presence and clarity of the plan*, which is checkable — not comprehension, which is not.
- The gate-coverage view (Part A) treats a stage with no `objective` as a coverage gap, same as a missing gate.

### 2. Small vertical slices

The article's "100 lines at a time, not 2000 at the end." A stage carries a `slice_budget` (lines/files/artifact size); exceeding it **routes to the funnel's `review` layer** (or fails a `--require`-style floor) rather than proceeding straight to a human `approve`. This is a policy on top of the existing `route_back_at` machinery — the oversized-slice telemetry (Part B) becomes an enforced budget rather than a passive count.

### 3. Cited change descriptions (an example decision gate)

A change should ship a description of *what it does, citing the code it touches* — the artifact that demonstrates the author (human or agent) actually traced the change. This is a natural **decision gate** on a `document-writer`-produced rationale:

- Required artifact: a `change-rationale` (markdown) with a **structured citation list** (`path:line` anchors into the diff).
- A `cited-change` decision skill verifies every claimed effect cites a real, in-diff location, and that the diff's touched files are covered. It gates *citation coverage* (mechanical, honest) — a strong proxy for "the description reflects the actual change," while making no claim about whether a human read it.
- Feeds the `uncited-change` signal (Part B).

### 4. Recurring expert-persona repo audits

The every-other-week whole-repo audit — expert personas whose objective is to *find issues and enforce best practices* — is the mechanism that catches accumulated debt no per-change gate sees. Today it's run by hand (the `/code-review ultra` flow). Make it a first-class **scheduled swarm**:

- A `cron` **Trigger** (the schema already supports `type: cron`) fires an **audit topology**: a panel of expert-persona reviewer archetypes (security, architecture/maintainability, performance, API-consistency, test-coverage), each a harness or model node with read-only IAM, tasked to *find issues and cite them*.
- Findings land in the audit log and the **skill-gap / comprehension log**; high-severity findings open a human review task. The `stale-audit` signal (Part B) fires if the cadence lapses.
- This is the natural home for a maintainability rubric — the thing no per-diff shape-gate can measure but a periodic whole-repo expert pass can. It is SwarmKit dogfooding its own thesis: the audit swarm is itself a topology, versioned and diffable.

## API shape

```yaml
# StageGraph stage — plan-first + slice discipline (additive, optional)
stages:
  - id: build
    topology: oms-build-harness
    when: [design.approved]
    objective: "Implement the reserved-stock API per contract oms-inventory."   # NEW
    acceptance: "SIT green + contract honored"                                    # NEW
    slice_budget: { max_diff_lines: 400 }                                         # NEW → routes to review if exceeded
    gate: oms-code-review
    success: build.ready-in-qa
```

```yaml
# A recurring expert-persona audit — a cron Trigger targeting an audit topology
apiVersion: swarmkit/v1
kind: Trigger
metadata: { id: fortnightly-audit }
type: cron
config: { schedule: "0 6 */14 * *" }
targets:
  - topology: repo-audit-panel        # expert-persona reviewers, read-only, "find issues + enforce best practices"
```

```bash
# CLI — thin client over the WorkspaceRuntime service layer
swarmkit gates <workspace> <pipeline>          # gate-coverage table + narrowest-edge verdict; --require <floor> gates CI
swarmkit comprehension <workspace>             # comprehension-debt signals, report-only by default
```

```text
# serve — the SAME service-layer methods, surfaced for the web app (single-instance) + fleet panel
GET  /api/pipelines/{id}/gate-coverage         # → CLI `swarmkit gates`  → canvas overlay
GET  /comprehension                            # → CLI `swarmkit comprehension` → comprehension panel
GET  /review                 + POST /review/{id}/{approve|reject}   # resolve human/funnel/multi-party gates in the UI
# gate CONFIG is not new API: funnels, approval policy, role registry, and the stage
# objective/acceptance/slice_budget are artifacts edited through the existing composer/canvas CRUD.
```

- **Schema:** three optional stage fields (`objective`, `acceptance`, `slice_budget`) — a `stage-graph` schema change under `schema-change-discipline.md`. Everything else (gate-coverage, telemetry, the audit topology) is read-side or ordinary artifacts — **no schema change**.
- **Serve UI follows the schema — mandatory.** A schema change here is not complete until the **serve composer/canvas renders and edits the new fields** (`objective`/`acceptance` as text, `slice_budget` as its nested object) through the schema-form surface, in the same slice. The `schema-change-discipline` checklist (canonical + bundled copy + fixtures + pydantic/TS codegen) is necessary but **not sufficient** — the TS types regenerate, but the composer form and canvas node-panel must be *verified* to actually surface the field (schema-driven forms don't always render a new shape correctly, e.g. nested objects). No user-authored field ships editable-only-by-hand-YAML — the CLI and web UI are peer clients over the same service layer (`docs/notes/usability-first.md`). This is added to `schema-change-discipline.md` as a standing step, not just recorded here.
- **Reference skills:** `plan-objective`, `cited-change` (decision category) + a `repo-audit-panel` reference topology. Reference artifacts, not runtime code.

## Non-goals

- **A comprehension score or a comprehension gate.** We measure and cultivate leading indicators; we never claim to verify understanding. Any check that purports to is out of scope by design.
- **Blocking by default.** Part A can gate CI *on demand* (`--require`); Part B is report-only unless a workspace opts a signal into a threshold. No new silent block.
- **New capture infrastructure.** Telemetry is derived from the existing audit log; if a signal needs data the audit doesn't have, that's a separate `human-interaction-model.md` change, called out explicitly, not smuggled in here.
- **Replacing human judgment.** The audit panel and the signals *surface*; humans decide. Consistent with §8 and the funnel invariant.
- **A CLI-only surface.** Every read, every gate action (approve/reject, answer a relay), and every gate configuration in this note is available in the serve web app — and, for reads, the fleet panel. The CLI is a peer client over the same service layer, never the sole one (`cli-architecture`). Any capability that lands CLI-first must land in serve in the same slice.
- **Growing or stacking the funnel.** `funnel:` / `gate:` stay a single ref and the funnel's automated layers stay single-slot — a funnel is a hard human gate, full stop. Multiple or agent-specific automated gates belong in `decision_skills[]`, never in more funnels or array-ified funnel layers (see *Gate composition* above).

## Test plan

- **Unit:** gate-coverage classifier over crafted StageGraph/topology fixtures (every class incl. `passthrough`, external-event edges, nested funnels); `--require` floor exit codes; each telemetry signal from synthetic audit fixtures (fast-approve boundary, oversized-slice, uncited-change).
- **Integration:** `swarmkit gates` on the SDLC example prints the real coverage map; `swarmkit comprehension` over a recorded audit log; the `cron` audit trigger fires the audit topology and lands findings in the log.
- **Test data:** extend the SDLC example — introduce a deliberate `passthrough` edge and assert the verdict names it; a `change-rationale` fixture with a broken citation.

## Demo plan

- `swarmkit gates examples/sdlc-pipeline/workspace sdlc-full` prints the coverage table and the narrowest-edge verdict; the pipeline canvas shows one red `passthrough` edge, then green after a funnel is added.
- `just demo-audit` runs the `repo-audit-panel` over a small fixture repo and shows expert-persona findings + a `stale-audit` clock.
- A short recorded transcript of `swarmkit comprehension` flagging a fast-approve on a deliberately oversized diff.

## Implementation plan

Sliced like the SDLC example — each slice is one reviewable PR with tests + a demo, each obeys **Surface parity** (CLI *and* serve) and the schema→UI discipline, and the order is read-only-first / composition-first / schema-last so risk climbs slowly. Per **Applicability**, every slice ships *visibility* first; `--require` floors and decision-thresholds land as opt-in enforcement.

**Taxonomy correction (found during seam-mapping).** A `Funnel` always ends in a required human `approve` (`funnel.raw.approve` is non-optional), so a stage edge with a `gate:` is always **human**; `validate` / `judge` / `review` are *pre-filter strength* on top of that human gate, not weaker alternatives to it. Automated-only gates exist, but they come from `decision_skills[]` on nodes (Gate composition), not from a stage's funnel. So a stage edge classifies as: `passthrough` (no `gate:`), `human` (a funnel — annotated with the pre-filters present), or `external` (the entry `when` is an outside event: CI / a rig / SAST). The "narrowest verified edge" verdict prioritizes `passthrough`, then thin pre-filtering.

- **Slice 1 — Gate coverage (read-only, no schema change).** A pure `compute_gate_coverage(ResolvedWorkspace, pipeline_id)` over `ws.stage_graphs[id].raw.stages` + `ws.funnels` (mirrors the ref-check in `resolver/_stage_graphs.py`); classifies each edge per the taxonomy above and names the narrowest. `swarmkit gates` CLI (template: `cli/_cmd_authoring.py:validate`) + `GET /api/pipelines/{id}/gate-coverage` (template: `server/_routes_introspection.py`, via `_get_runtime(request).workspace`). Both call the one pure function.
- **Slice 2 — Coverage on the canvas.** Color pipeline-editor edges by class; `passthrough` red. The serve-UI half of Slice 1's parity.
- **Slice 3 — Comprehension telemetry (read-only).** `swarmkit comprehension` + `GET /comprehension`, signals computed from `_observability.Observability.query_audit`; report-only. fast-approve / oversized-slice / unreviewed-merge first (derivable today); uncited-change / stale-audit follow their Part-C dependencies.
- **Slice 4 — Comprehension panel in serve UI.** Parity for Slice 3. The fast-approve threshold is shown as a **pre-set (populated) value**, read from the endpoint's `threshold_seconds`, not an empty placeholder — the user must see the active value, and editing it re-queries `GET /comprehension?fast_approve_seconds=`.
- **Slice 5 — `cited-change` (composition-only).** A `change-rationale` document + a `cited-change` reference decision skill (the `judge` half) + the deterministic citation-coverage core (`cited_change.py`: unified-diff parse + citation check) surfaced as `swarmkit cited-change` (CI-gatable, exit 1 on an uncited change) — *you cannot cite code you did not open*. No schema change. (Wiring the deterministic check as a first-class funnel `validate` layer is a follow-on — funnel `validate` is schema-only today.)
- **Slice 6 — Recurring expert-persona audit.** A `repo-audit-panel` reference topology + a `cron` Trigger; findings → audit + review queue; lights up `stale-audit`.
- **Slice 7 — `slice_budget` (first schema change).** Optional `slice_budget` on the stage; the runtime measures the produced diff/artifact and routes over-budget output to the funnel `review` layer. Full schema discipline **+ the composer form for the new field** (schema→UI step 6).
- **Slice 8 — plan-first (schema + gate).** Optional `objective` / `acceptance` on the stage; a `plan-objective` decision-skill binding at stage entry; gate-coverage flags a missing objective. Schema discipline **+ composer form**.

## Open questions

1. **Read-time estimation** for fast-approve — bytes/complexity heuristic, or configurable per-workspace? A bad estimate produces false rubber-stamp flags. Start conservative and report-only.
2. Does `slice_budget` belong on the **stage**, the **funnel**, or both? Leaning stage (it's a plan property), but a funnel-level default is tempting for reuse.
3. Should the gate-coverage floor ever be **enforced at author time** (reject a StageGraph with a `passthrough` edge on a `topologies:modify`) rather than only in CI? That would make "govern the graph's evolution" real — but risks over-gating early exploration. Probably a workspace policy, off by default.
4. Maintainability rubric for the audit panel: bootstrap from an existing best-practices checklist, or grow it from the skill-gap log over time?
