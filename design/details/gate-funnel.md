# Gate funnel (per-artifact quality gate composition)

Parent: `design/details/sdlc-pipeline-example.md` (capability 2 of 5). Standalone and reusable —
any workspace can put a funnel on any artifact-producing node; the SDLC pipeline is the first
consumer.

Every artifact worth a human sign-off should reach that human already having cleared the cheap,
automatable checks. This note defines the **composition** — a declarative, per-artifact gate that
chains *structured-output validation → LLM-as-judge → (optional) harness review → human approval*
into one reusable unit, with the control flow, the bounded retry loop, and the structural
invariant that the automated layers **filter but never decide**.

It composes existing pieces and sibling capabilities; it does not redefine them:
- **Layer 1 — structured-output validation:** existing (`constrained-output-schema.md`,
  structured-output governance).
- **Layer 2 — LLM-as-judge:** an existing governance **decision skill** (`decision-skills.md`,
  `governance-decision-skills.md`), instantiated as the rubric-parameterised `artifact-judge`.
- **Layer 3 — harness review:** sibling `design/details/harness-reviewer.md`.
- **Layer 4 — human approval:** sibling `design/details/multi-party-approval.md`.

## Goal

Make "every artifact is judged before a human sees it" a **declarative property of a gate**, not
bespoke wiring per stage. One funnel schema, applied per artifact, that compiles to a gate whose
only exit to human approval is *through* the automated layers — cheap → expensive → human.

## Non-goals

- **Not the layers' internals.** The judge rubric, the reviewer archetype, and the approval policy
  are defined by their own notes/skills; the funnel only *sequences and gates* on them.
- **Not stage sequencing.** How a failed/exhausted funnel routes across stages, and the cross-stage
  defect loop, are `pipeline-controller`. The funnel's scope is one artifact, one gate.
- **Not a new judge or validator engine.** It reuses decision skills + structured-output validation
  as-is.

## Where it lives

The funnel is a **governance gate composition**: its schema is gate configuration, and it compiles
(runtime) to a subgraph whose control flow *structurally* routes through the human interrupt — there
is no compiled edge that reaches "done" while skipping human approval. The advisory invariant is
enforced by graph shape, not by prompt.

## API shape

### The funnel schema (per artifact)

Each layer is **optional except `approve`** (a funnel with only `approve` degrades to a plain
multi-party gate). Present layers run in order.

```yaml
gate: consolidated-design-approval
artifact: consolidated-design
funnel:
  validate:                              # layer 1 — deterministic, no LLM
    schema: schemas/consolidated-design.json
    autocorrect: true                    # field-level repair (Rynko); unrepairable → retry
  judge:                                 # layer 2 — governance decision skill
    skill: artifact-judge
    rubric: rubrics/consolidated-design.md
    threshold: 0.8                        # below → retry
    max_retries: 2
  review:                                # layer 3 — optional; heavyweight gates only
    archetype: architect-reviewer
    read_scope: [app:oms, app:web, app:mobile]
    route_back_at: high                   # findings >= this severity retry; others attach
  approve:                               # layer 4 — the multi-party approval set (sibling note)
    rules:
      - { scope: design:approve,   roles: [oms-lead, web-lead, mobile-lead], quorum: all }
      - { scope: security:approve, roles: [infosec-lead],                    quorum: all }
```

### Control flow

```
draft ─▶ validate ─(ok)▶ judge ─(pass)▶ review ─(no route-back)▶ APPROVE (human) ─▶ done
           │                │                     │
       (unrepairable)   (below threshold)   (finding ≥ route_back_at)
           └──────────────┴─────────────────────┘
                          ▼
                    retry: critique/findings ─▶ drafting agent revises ─▶ re-enter at validate
```

- **validate**: schema-checks + auto-repairs the draft; an unrepairable shape is a retry (the judge
  never sees malformed input — kills shape hallucination up front).
- **judge**: scores against the rubric; `< threshold` is a retry carrying the critique.
- **review** (optional): the harness reviewer investigates and returns findings; findings at or
  above `route_back_at` retry (carrying the findings), the rest **attach** and travel to the human.
- **approve**: the binding human layer (per-role tasks, quorum, `min_distinct_approvers` — sibling
  note). The only edge to `done`.

### The retry loop (bounded; exhaustion escalates, never drops)

A retry feeds the failing layer's critique/findings back to the **drafting agent**, which revises
and re-enters at `validate`. Bounded by `max_retries`. On **exhaustion** the funnel does **not**
drop the requirement or silently pass — it **escalates to a human** with the last failing critique
attached, and that human decides (force-advance to approval, or reject). Retry state lives in the
run checkpoint, so it is durable and resumable.

### The advisory invariant (structural)

Layers 1–3 are **advisory**: they gate *advancement to* human approval and drive the retry loop,
but they **never** approve. Two properties, enforced by the compiled graph shape (not prompt):

1. **No bypass of the human gate.** There is no edge from any automated layer to `done`; the only
   path to `done` is through `approve`. A judge/reviewer "pass" advances *to* the human, never *past*
   them.
2. **No reaching the human without passing.** `approve` is only reachable after `validate` + `judge`
   succeed (and non-blocking review has attached). A below-threshold artifact cannot land on a
   human's desk except via the explicit retry-exhaustion escalation.

This is the judicial pillar (§8) filtering for the legislative/human one, not substituting for it.

### Provenance bundle (what the human sees)

On reaching `approve`, the funnel assembles a bundle the human task carries: the artifact, the
validation result, the judge score + critique, the attached reviewer findings, the retry count, and
the diff-since-last-approval. `task-surface-and-board` renders it; the funnel produces it, so a human
decides in one place with the full automated context.

## Eject

The funnel ejects as a LangGraph subgraph: a validate node, a judge node with a conditional edge
(`pass → next`, `fail → drafter`, guarded by a retry counter in state), an optional review node, and
the human `interrupt()` for approval — with **no** edge skipping the interrupt. The advisory
invariant is therefore visible in the generated code, satisfying invariant 7.

## Test plan

- **Schema (Python + TS):** a funnel with only `approve` validates (degenerate = plain gate); a
  funnel referencing an unknown skill/archetype is rejected; layer ordering is fixed regardless of
  key order.
- **Control flow (integration):** a malformed field is auto-corrected and proceeds; a below-threshold
  draft triggers a retry whose revision then passes and reaches the human; a `route_back_at`-severity
  finding retries while a low finding attaches and proceeds.
- **Advisory invariant (the load-bearing tests):** there is *no* execution path from a judge/review
  pass to `done` without the human interrupt; `approve` is unreachable while `judge` is below
  threshold — asserted on the compiled graph, not just at runtime.
- **Retry exhaustion:** after `max_retries` the funnel escalates to a human with the last critique
  attached — it neither loops forever nor drops nor silently advances.
- **Provenance:** the human task carries artifact + validate result + judge score + findings + retry
  count + diff.
- **Eject:** the generated subgraph contains the human interrupt on every path to `done`.

## Demo plan

`just demo-gate-funnel`: a single artifact through a full funnel — show (a) an auto-corrected field,
(b) a judge fail → auto-retry → pass, (c) a reviewer finding attaching to the human task, (d) the
human approval as the sole exit, and (e) a second run where retries exhaust and the gate escalates to
a human instead of dropping. Terminal transcript in the PR body.

## Schema-change checklist

Adds a `funnel` gate sub-schema (composing the `approval` block from `multi-party-approval`) —
follow `docs/notes/schema-change-discipline.md`: canonical JSON Schema, Python + TS validators, and
fixtures updated together.
