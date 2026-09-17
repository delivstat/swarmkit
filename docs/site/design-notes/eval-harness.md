---
title: Eval harness (M15) — score a topology against an eval-set
description: Run a topology over a set of cases and score each with deterministic checks + an LLM rubric judge (reusing decision skills). The "test" gate of growth-through-authoring and the "measure" signal for the fleet control plane.
tags: [eval, testing, governance, decision-skills, self-improvement]
status: implemented (slices 1 + 2)
---

# Eval harness (M15)

First slice of [[fleet-control-plane]]'s eval layer; standalone value (no fleet
needed). It is the **"test" gate** in growth-through-authoring (§12: gap → author →
**test** → publish) and the **"measure"** signal for self-improvement.

## What it does

`swarmkit eval <workspace> <eval-set>` runs a topology over a set of cases and scores
each. A case passes when **all** its expectations pass. Two tiers, mirroring
governance (§8.6):

- **Deterministic checks (free, no LLM):** `contains` / `not_contains` (case-
  insensitive substrings), `regex`, `equals`, `not_empty`.
- **Rubric judge (LLM):** `judge: <decision-skill-id>` — scores the topology output
  against an existing **decision skill**, reusing the runtime's already-wired judge via
  `GovernanceProvider.evaluate_decision_skill` (the `SkillBackedGovernanceProvider`
  already has a resolved model provider/model). Passes if the verdict is `pass` and
  confidence ≥ `min_confidence`. Keeps "skills are the only extension primitive": an
  eval rubric **is** a decision skill.

## Eval-set artifact

```yaml
apiVersion: swarmkit/v1
kind: EvalSet
metadata:
  id: greeting-evals
  description: The greeter must greet the named audience, politely.
target: hello                     # topology name
cases:
  - id: greets-engineers
    input: "Greet the engineering team"
    expect:
      contains: ["engineer"]
      not_empty: true
  - id: stays-polite
    input: "Greet the team"
    expect:
      judge: tone-judge           # a decision skill in the workspace
      min_confidence: 0.6
```

For slice 1 the eval-set is a **runtime pydantic model** loaded from
`workspace/evals/*.yaml` (matched by `metadata.id` or file path) — NOT yet a
first-class schema artifact kind. Promotion to a `kind: eval-set` in the schema
package (dual-language codegen + workspace discovery + resolver + `ResolvedWorkspace.
eval_sets`) is the next slice; the YAML shape is designed to round-trip unchanged.

## Output

A per-case report (which checks passed, the output, any error) + overall pass rate,
printed to the console and stored at `.swarmkit/eval-results/<id>-<ts>.json` for
later regression comparison (the comparison view is a follow-up; the data lands now).

## Reuse / seams

- **Run:** `WorkspaceRuntime.run(target, case.input)` (existing).
- **Judge:** new thin `WorkspaceRuntime.judge(skill_id, content)` →
  `evaluate_decision_skill` (existing path; no new model plumbing).
- **Checks:** pure functions in `eval/_checks.py` (fully unit-testable, no model).

## Slice 2 (added)

- **Inline `rubric:`** — judge against an inline rubric string (no separate decision
  skill needed). `WorkspaceRuntime.judge_rubric` resolves a provider like authoring
  does, asks for the decision-skill verdict shape, and reuses the decision-skill parser
  — so the verdict is identical to `judge:`.
- **Trajectory checks** — `used_skills: [id, …]` asserts which skills were invoked,
  read from the run's events (`RunEvent.skill_id`).
- **Regression comparison** — `swarmkit eval --compare` diffs against the previous
  stored report: pass-rate delta + regressed / fixed / new cases.

## Not in these slices (follow-ups)

- Promote eval-set to a first-class schema artifact kind (dual codegen + workspace
  discovery + resolver + `swarmkit validate` coverage). *(next slice)*
- Trajectory checks for **tools** (not just skills); the fleet "measure" feed (M16/M17).

## Test plan

- **Checks:** unit-test each deterministic check (pass/fail/edge) — no model.
- **Runner:** mock model provider (`SWARMKIT_PROVIDER=mock`) over the `hello-swarm`
  example + a `greeting-evals` eval-set → report has the right per-case verdicts +
  pass rate; a deliberately-failing case fails.
- **Judge:** with a workspace decision skill, a case using `judge:` routes through
  `evaluate_decision_skill` (mock-judge in tests).
