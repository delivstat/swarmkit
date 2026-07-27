---
title: Deterministic validate checks in a funnel (cited-change + slice_budget)
description: Wire the deterministic cited-change and slice_budget checks into a funnel's validate layer as sibling keys — single-slot preserved, failures drive the funnel's existing bounded retry → human escalation.
tags: [governance, funnel, stage-graph, gate]
status: draft
---

# Deterministic validate checks in a funnel (cited-change + slice_budget)

**Scope:** `governance` (`langgraph_compiler/_gate_funnel.py`), `schema` (funnel). Extends an
already-shipped surface; no new artifact kind.
**Design reference:** the runtime-enforcement follow-on recorded in
`gate-coverage-and-comprehension-debt.md`; builds on `gate-funnel.md`. §8 governs.
**Status:** draft (design-only) — review before implementation.

## Goal

Let a funnel's `validate` layer run the **deterministic** checks shipped as `swarmkit slice-check`
and `swarmkit cited-change` (`slice_budget.py`, `cited_change.py`), so an over-budget or uncited
change is caught **inside the gate** — driving the funnel's existing bounded retry, then escalating
to the human `approve` layer — instead of only via a standalone CLI/CI step.

## Decision (settled): sibling keys, not an array

Per the gate-composition principle (`gate-coverage-and-comprehension-debt.md`), a funnel's
`validate` / `judge` / `review` stay **single-slot pre-filters** — never grown into an array of
layers. So the checks attach as **optional sibling keys on the one `validate` block**, not a
`checks: []` array:

```yaml
validate:
  schema: schemas/diff-result.json   # existing — JSON-schema shape check
  autocorrect: true                  # existing
  slice_budget:                      # NEW — enforce a slice-size budget on the produced diff
    max_diff_lines: 400
    max_files: 20
  cited_change: true                 # NEW — run citation-coverage on the change-rationale + diff
```

This is still **one** validate layer (a fixed deterministic pre-filter); it just runs schema +
whichever deterministic checks are declared. It is not a composition surface — for *more* or
*agent-specific* checks you still add a `decision_skills[]` binding, unchanged.

## The seam problem (why this needs design, not just code)

Today the validate layer is injected as:

```python
Validator = Callable[[str], Awaitable[ValidateOutcome]]   # _gate_funnel.py
# validate_node: out = await validator(state.get("artifact", ""))
```

It receives **one artifact string** (the current draft). That fits the checks unevenly:

- **`slice_budget`** — needs the **diff**. If the gated node is a harness executor, its produced
  artifact *is* (or contains) a unified diff, so `slice_budget` parses `artifact` directly. Clean
  fit, no seam change.
- **`cited_change`** — needs **two** inputs: the change-rationale (with citations) *and* the diff.
  A single `artifact` string can't be both. This requires **widening the validate seam** to pass a
  small context object.

**Proposed seam:**

```python
@dataclass(frozen=True)
class ValidateContext:
    artifact: str                 # the current draft (unchanged meaning)
    diff: str | None = None       # the produced unified diff, when the node emits one (harness)
    rationale: str | None = None  # a change-rationale artifact, when the node produces one

Validator = Callable[[ValidateContext], Awaitable[ValidateOutcome]]
```

Backward-compatible in behaviour: the existing schema validator ignores `diff`/`rationale` and reads
`ctx.artifact` exactly as before. Only the *call site* changes (`validator(ctx)` instead of
`validator(artifact)`), and the drafter/harness node populates `diff`/`rationale` into the gate
state when it has them (a `model` node leaves them `None`, so `cited_change`/`slice_budget` on a
non-diff-producing node is a load-time validation error, not a silent no-op).

## Failure semantics (reuse, don't invent)

A failed deterministic check is **exactly a failed validate today**: `ValidateOutcome(ok=False,
feedback=…)` routes back to the drafter with the check's message ("over slice budget: 812 lines >
400 — split it" / "uncited change: 2 claims cite lines the diff didn't change") for a **bounded
retry**; on `max_retries` exhaustion the funnel **escalates to the human `approve` layer** with the
feedback attached — never drops, never advances. Ordering within the single layer: `schema →
slice_budget → cited_change`; the first failure produces the retry feedback.

## `slice_budget`'s two homes (resolved)

`slice_budget` already exists as a **stage** field (slice 7). Resolution:

- **stage `slice_budget`** = the *declaration* — what gate coverage reads and reports ("no
  objective", budget shown); advisory, not enforced by itself.
- **funnel `validate.slice_budget`** = the *enforcement* — the gate that actually blocks/retries.

If a stage sets a budget *and* its gate funnel sets one, the **funnel's enforces**; a resolver
lint warns when they disagree (so the declaration and the enforcement don't silently drift). A
stage with a budget but a gate funnel without one is a coverage gap the `gates` view already shows.

## API shape

- **Schema:** two optional keys on the funnel `validate` block — `slice_budget`
  (`{max_diff_lines?, max_files?}`, reusing the stage-graph shape) and `cited_change` (boolean).
  Full schema-change discipline (canonical + bundled + valid/invalid fixtures + pydantic/TS codegen
  + the funnel-editor composer form, schema→UI step 6). Funnel schema minor-bump.
- **Runtime:** widen `Validator` to take `ValidateContext`; a `build_deterministic_validator(spec)`
  that composes schema + slice_budget + cited_change into one `Validator`, reusing
  `slice_budget.check_slice_budget` and `cited_change.check_citations` (no logic duplicated). The
  harness node populates `ctx.diff` / `ctx.rationale`.

## Non-goals

- No `checks: []` array; `judge` / `review` stay single-slot (unchanged).
- No new artifact kind; the change-rationale stays an embedded document (`cited_change.py`).
- Not re-implementing the checks — the validate layer *calls* the slice-5/slice-7 pure functions.

## Test plan

- **Unit:** `build_deterministic_validator` over a `ValidateContext` — slice_budget over/under, an
  uncited vs cited rationale+diff, schema+deterministic composition, first-failure feedback.
- **Integration:** a funnel with `validate.slice_budget` gating a harness node that emits an
  over-budget diff → retry with feedback, then escalate to `approve` on exhaustion (fakes at the
  drafter/approver seams, per `_gate_funnel.py`'s existing test style).
- **Schema:** valid fixture (funnel with both keys) + invalid (`slice_budget.max_diff_lines: 0`).

## Demo plan

`just demo-funnel-validate`: a funnel whose validate declares `slice_budget`; feed it an
over-budget diff and show the bounded retry + escalation transcript (deterministic, no keys).

## Open questions

1. **Seam change blast radius** — every `compile_funnel_gate` caller + the schema-validate injection
   must move to `ValidateContext`. Confirm no other consumer passes a bare artifact string.
2. **How the harness node surfaces `diff` / `rationale`** into the gate state — the concrete
   plumbing from the executor's produced diff + the node's structured output. This is the crux; it
   may deserve its own small slice.
3. **`cited_change` rationale source** — is the rationale the node's `artifact` (so `ctx.rationale
   == ctx.artifact`) or a separate produced document? Affects whether cited_change needs the seam
   widening at all, or just a diff alongside the artifact.
