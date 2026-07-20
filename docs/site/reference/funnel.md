# Funnel

A **funnel** is a first-class SwarmKit artifact (`kind: Funnel`) that packages a per-artifact quality gate. It chains up to four layers — structured-output validation, an LLM-as-judge, an optional harness review, and multi-party human approval — into one reusable composition. Any node that produces an artifact worth a human sign-off can reference a funnel by id, so the same gate applies consistently across nodes and stages instead of being re-wired per stage.

The full composition, control flow, bounded retry loop, and the structural invariant are specified in the design note: [gate funnel](https://github.com/delivstat/swarmkit/blob/main/design/details/gate-funnel.md) (`design/details/gate-funnel.md`). This page is the artifact reference.

## What a funnel is for

Every artifact worth a human sign-off should reach that human having already cleared the cheap, automatable checks. A funnel makes "every artifact is judged before a human sees it" a declarative property of a gate rather than bespoke wiring per stage: the only path to approval is *through* the automated layers, cheap → expensive → human.

A funnel does not redefine the layers it sequences — it reuses SwarmKit's native structured-output validation, an existing governance decision skill for the judge, a harness reviewer archetype, and the multi-party approval policy. It only sequences and gates on them.

## Referenced by id

A funnel is a standalone artifact, like a skill or an archetype. It lives in a `funnels/` directory in the workspace and is referenced by id from a topology node's `funnel:` field. Defining it once and referencing it by id is what lets one gate cover many nodes and stages.

## The layers

Present layers always run in the fixed order `validate → judge → review → approve`. Key order in the YAML does not matter — the control flow is compiler-owned. Every layer is optional **except `approve`**; a funnel with only `approve` degrades to a plain multi-party approval gate.

| Layer | Kind | What it does |
|---|---|---|
| `validate` | deterministic, no LLM | Structured-output validation against a JSON Schema with field-specific auto-correction. A shape auto-correction cannot repair is a retry — the judge never sees malformed input, which kills shape hallucination up front. |
| `judge` | LLM-as-judge decision skill | Scores the artifact against a rubric. A score below `threshold` drives a bounded retry carrying the critique back to the drafter. |
| `review` | harness reviewer (optional, heavyweight) | An investigative reviewer returns findings. Findings at or above `route_back_at` severity retry; the rest attach to the human task and travel onward. |
| `approve` | multi-party human approval (**required**) | The binding human layer: per-role tasks, quorum, `min_distinct_approvers`, `exclude_author`. The only exit from the funnel to `done`. |

## The fixed control flow

```
draft ─▶ validate ─(ok)▶ judge ─(pass)▶ review ─(no route-back)▶ APPROVE (human) ─▶ done
           │                │                     │
       (unrepairable)   (below threshold)   (finding ≥ route_back_at)
           └──────────────┴─────────────────────┘
                          ▼
                    retry: critique/findings ─▶ drafting agent revises ─▶ re-enter at validate
```

A retry feeds the failing layer's critique or findings back to the drafting agent, which revises and re-enters at `validate`. Retries are bounded by the judge's `max_retries`. On **exhaustion** the funnel does not drop the requirement or silently pass — it escalates to a human with the last failing critique attached, and that human decides. Retry state lives in the run checkpoint, so it is durable and resumable.

## The structural invariant

Layers 1–3 are **advisory**: they gate *advancement to* human approval and drive the retry loop, but they **never** approve. Two properties, enforced by the compiled graph shape (not by prompt wording):

1. **No bypass of the human gate.** There is no edge from any automated layer to `done`; the only path to `done` is through `approve`. A judge or reviewer "pass" advances *to* the human, never *past* them.
2. **No reaching the human without passing.** `approve` is only reachable after `validate` and `judge` succeed (and any non-blocking review has attached). A below-threshold artifact cannot land on a human's desk except via the explicit retry-exhaustion escalation.

This is the judicial pillar (design §8) filtering for the human/legislative one, not substituting for it. The control flow is fixed and compiler-owned: a funnel configures the layers, it does not rewire the graph.

## Provenance bundle

On reaching `approve`, the funnel assembles a bundle the human task carries: the artifact, the validation result, the judge score and critique, the attached reviewer findings, the retry count, and the diff since the last approval. The human decides in one place with the full automated context.

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: Funnel
metadata:
  id: <lowercase-kebab>      # referenced from a node's funnel: field
  name: <human name>
  description: <what this gate protects>
validate:                    # optional — layer 1
  schema: <workspace-relative JSON Schema path>
  autocorrect: true          # default true
judge:                       # optional — layer 2
  skill: <decision-skill id>
  rubric: <workspace-relative rubric path>
  threshold: 0.8             # default 0.8; below → retry
  max_retries: 2             # default 2; then escalate to a human
review:                      # optional — layer 3
  archetype: <reviewer archetype id>
  read_scope: [<scope>, ...] # read-only IAM scopes for the investigation
  route_back_at: high        # default high; findings >= this retry, rest attach
approve:                     # REQUIRED — layer 4
  rules:                     # every rule must be satisfied
    - scope: <area:action>
      roles: [<role id>, ...]
      quorum: all            # all | any | { k-of: N }
  exclude_author: true       # default true — segregation of duties
  on_revision: reset_all     # default reset_all | reconfirm_changed
  min_distinct_approvers: 2  # optional four-eyes floor
provenance:
  authored_by: human
  version: 1.0.0
```

## Minimal example

A funnel with only `approve` is valid — it degrades to a plain multi-party approval gate:

```yaml
apiVersion: swarmkit/v1
kind: Funnel
metadata:
  id: design-signoff
  name: Design Sign-off
  description: A plain multi-party human approval gate on the design artifact.
approve:
  rules:
    - scope: design:approve
      roles: [tech-lead]
      quorum: all
provenance:
  authored_by: human
  version: 1.0.0
```

## Full example

All four layers, as used on the consolidated-design artifact in the SDLC pipeline example ([`examples/sdlc-pipeline/workspace/funnels/consolidated-design-approval.yaml`](https://github.com/delivstat/swarmkit/blob/main/examples/sdlc-pipeline/workspace/funnels/consolidated-design-approval.yaml)):

```yaml
apiVersion: swarmkit/v1
kind: Funnel
metadata:
  id: consolidated-design-approval
  name: Consolidated Design Approval
  description: >
    Full four-layer gate — deterministic schema validation, an LLM-as-judge
    rubric score, an architect harness review, then a multi-party human approval
    that is the only exit.
validate:
  schema: schemas/consolidated-design.json
  autocorrect: true
judge:
  skill: artifact-judge
  rubric: rubrics/consolidated-design.md
  threshold: 0.8
  max_retries: 2
review:
  archetype: architect-reviewer
  read_scope: [app:oms, app:web, app:mobile]
  route_back_at: high
approve:
  rules:
    - scope: design:approve
      roles: [oms-lead, web-lead, mobile-lead]
      quorum: all
    - scope: security:approve
      roles: [infosec-lead]
      quorum: all
  exclude_author: true
  min_distinct_approvers: 2
provenance:
  authored_by: human
  version: 1.0.0
```

## Authoring a funnel

The conversational authoring path treats a funnel like any other artifact: the schema drafter calls `get_schema("funnel")` for the exact shape, and `query-swarmkit-docs` surfaces this reference and the design note. When authoring a funnel, decide which layers the artifact warrants (`review` is for heavyweight gates only), keep the fixed `validate → judge → review → approve` semantics in mind, and remember that `approve` is required and the funnel is referenced by id from the node it gates.

## See also

- [Gate funnel design note](https://github.com/delivstat/swarmkit/blob/main/design/details/gate-funnel.md) — the authoritative composition, control flow, bounded retry, structural invariant, and provenance bundle.
- [Skills](skills.md) — the decision skill the `judge` layer instantiates.
- [Archetypes catalogue](archetypes.md) — the reviewer archetype the `review` layer uses.
