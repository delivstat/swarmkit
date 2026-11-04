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

Eval sets are loaded from `workspace/evals/*.yaml` (matched by `metadata.id` or file
path) and validated against **`eval-set.schema.json`** in `packages/schema`, with
pydantic and TS codegen, so the CLI, serve and the portal accept exactly the same files
([[eval-set-schema-v1]]). The YAML shape above round-trips unchanged. Running eval sets
on serve, reports in the store and the portal's Evals page follow in
`design/details/eval-on-serve.md` (#1105).

## Output

A per-case report (which checks passed, the output, any error) + overall pass rate,
printed to the console. Slices 1–2 stored it at `.swarmkit/eval-results/<id>-<ts>.json`.
Since 1.267.0, every run is a row in the runtime store, run through `EvalService`.
Cases are recorded as they land, a run is resumable after a crash, and the CLI and serve
share one history ([[eval-on-serve]] PR 2). `--compare` still falls back to the newest
legacy file when the store has no earlier run of the set.

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
