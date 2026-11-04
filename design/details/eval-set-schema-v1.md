---
title: Eval-set schema v1
description: The EvalSet artifact as a JSON Schema — today's shape, made strict; unique case ids and target existence stay runtime checks.
tags: [schema, eval, eval-set]
status: implemented
---

# Eval-set schema v1

**Scope:** `packages/schema`, with the runtime loader (`packages/runtime/src/swarmkit_runtime/eval/_loader.py`)
**Design reference:** [[eval-harness]] (M15), [[eval-on-serve]] (PR 1 of 4), §12 (the test gate of growth through authoring)
**Status:** implemented

## Goal

Make the eval set a first-class, schema'd artifact, so the CLI, serve and the portal accept exactly the same files, and the portal's `SchemaForm` can render and edit one with no per-kind form code.

## Non-goals

- **New eval features.** The shape is the one `swarmkit eval` already runs. Parallel cases, captured cases and gate labels add fields when they ship ([[eval-on-serve]], #1105, #1106).
- **Workspace discovery and resolution.** Eval sets aren't part of `ResolvedWorkspace`. They're loaded when they're run, and in PR 3 serve lists them through `ArtifactService`.

## Shape

```yaml
apiVersion: swarmkit/v1
kind: EvalSet
metadata:
  id: support-triage-evals        # identifier — what `swarmkit eval <ws> <id>` names
  name: Support triage            # optional display name
  description: ...                # optional
target: support-triage@1.2.0      # a topology; `@x.y.z` pins one version
cases:                            # at least one
  - id: billing-goes-to-billing   # identifier, unique within the set
    input: I was charged twice.   # non-empty
    expect:                       # optional; empty or absent = smoke test
      contains: [billing]
      not_contains: [refund approved]
      regex: "(?i)billing"
      equals: billing
      not_empty: true
      used_skills: [classify-ticket]
      judge: triage-quality       # a decision skill
      rubric: Routes to billing.  # inline, no skill needed
      min_confidence: 0.7         # 0–1, default 0.5
provenance:                       # optional, the shared artifact provenance block
  authored_by: human
  version: 1.0.0
```

The field reference is the schema itself. Every field has a `title` and a `description`, which the portal shows.

## Decisions

- **Strict, like every other artifact schema.**
  - Unknown keys are refused at every level. Before this, the runtime model ignored unknown top-level keys and allowed extra `metadata` keys, so a typo such as `case:` for `cases:`, or `descripton:`, was dropped silently.
  - A set needs at least one case.
  - Case ids and the set id are lowercase-kebab identifiers, and a case input is non-empty.
  - All of these were accepted before. The runtime records the narrowing in `_breaking_changes.py`, and the loader's error names the field (`set.yaml @ /cases/0/expect: Additional properties are not allowed ('contain' was unexpected)`).
- **`target` allows `name@x.y.z`**, the form canary versions take in the workspace registry (`resolver/_topology.py:85`). Cases are never routed through a canary split, so a report scores one version.
- **Provenance is optional.**
  - Skills and archetypes require it (design §6.4) because they are capabilities that get published.
  - An eval set is a workspace's own test. Requiring provenance would break every existing set for no reader's benefit.
  - It's present so machine-captured sets (#1105) can say where they came from.
- **Two rules live in the runtime, not the schema.** JSON Schema can't express them.
  - **Unique case ids.** `uniqueItems` compares whole items, not one field, so the loader checks this. Reports and run-to-run comparisons are keyed by case id, so a duplicate would merge two cases' history.
  - **The target exists.** That's a reference into the workspace, checked when the set is run and, from PR 3, when serve writes one.
- **Reference hints.** `x-swarmkit-ref: topology` is on `target`, and `x-swarmkit-ref: skill` is on `judge` and on the items of `used_skills`. The portal renders these as pickers over the workspace.
- **The runtime keeps its own model.** `eval/_models.py` gives the runner plain types and defaults, where the generated model has root types and optional fields. The loader validates against the schema first and then narrows to the runtime model. `test_eval_set_schema.py` asserts that the two have the same fields at every level, so neither can drift from the other without a test failure.

## Test plan

- **Schema package, in Python and TS:**
  - three valid fixtures: minimal, every field, and a smoke-test case;
  - ten invalid fixtures, one per rule: a missing target, no cases, a bad set id, a bad case id, an unknown `expect` key, an unknown top-level key, an empty input, `min_confidence` above 1, a `@latest` target, and the wrong kind.
- **Runtime (`test_eval_set_schema.py`):**
  - every schema fixture loads or is refused through `load_eval_set`;
  - the shipped example sets load;
  - a schema error names the field;
  - a duplicate case id is refused;
  - `name`, a pinned target and `provenance` survive a load;
  - the model mirrors the schema.
- **Existing eval tests** (loader, runner, CLI, compare) pass unchanged.

## Demo plan

`examples/hello-swarm`'s `greeting-evals` set validates and runs unchanged:

```bash
uv run swarmkit eval examples/hello-swarm/workspace greeting-evals
```

A stray key shows the new error, naming the field:

```bash
uv run swarmkit eval <ws> ./bad-set.yaml
# error: ./bad-set.yaml @ /cases/0/expect: Additional properties are not allowed ('contain' was unexpected)
```
