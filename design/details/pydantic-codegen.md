---
title: Pydantic model codegen
description: Generate pydantic v2 models from the five canonical JSON Schemas. Shape-vs-full-validation split documented.
tags: [schema, codegen, pydantic, m0]
status: implemented
---

# Pydantic model codegen

**Scope:** `packages/schema/python/src/swarmkit_schema/models/`
**Design reference:** `docs/notes/schema-change-discipline.md` (the layering it described is now real).
**Status:** in review

## Goal

Generate typed pydantic v2 models from the five canonical JSON Schemas so runtime code works with typed objects instead of untyped `dict[str, Any]`. The JSON Schemas remain the source of truth; models are regenerated from them — never hand-edited.

## Non-goals

- **Replacing `validate()` as the authoritative validator.** `swarmkit_schema.validate(kind, data)` remains the single entry point that fully enforces the schema spec, including `allOf` / `if-then` rules that pydantic codegen does not translate. Runtime code calls `validate()` at artifact load time; once validated, data is loaded into pydantic models for typed access.
- **Hand-tuning the generated output.** If the generator's choice is awkward, we adjust the codegen invocation or the schema — never the output files.
- **TypeScript codegen.** Task #15, separate PR.

## The shape-vs-full-validation split

JSON Schema expresses constraints that pydantic natively supports (required fields, types, enums, patterns, min/max, discriminated unions) and constraints that pydantic does not natively support (`allOf` / `if-then`, cross-field conditional requirements). `datamodel-code-generator` faithfully translates the first group; the second group is silently dropped.

| Rule | jsonschema (authoritative) | pydantic model |
|---|---|---|
| Required fields, types, enums, patterns, `oneOf` discriminator | ✓ | ✓ |
| Per-field min/max, minItems, pattern | ✓ | ✓ |
| `allOf: if-then` cross-field rules (e.g. `type: plugin` ⇒ `provider_id` required) | ✓ | ✗ |

Concretely seven invalid fixtures from M0 PASS when instantiated as pydantic models but FAIL `validate()`:
- `skill-invalid/decision-missing-reasoning.yaml`, `decision-reasoning-wrong-type.yaml`
- `workspace-invalid/credential-plugin-missing-provider-id.yaml`, `mcp-http-missing-endpoint.yaml`, `mcp-stdio-missing-command.yaml`
- `trigger-invalid/cron-missing-config.yaml`, `plugin-missing-provider-id.yaml`

This split is acceptable for v1.0 because the authoritative validator runs before any pydantic model construction at runtime — by the time runtime code sees a pydantic object, `validate()` has already passed. The gap is documented here and in `docs/notes/schema-change-discipline.md` so future contributors don't mistake pydantic as complete.

### Decision — leave the gap open, invest in validation UX instead

We considered three paths to close it: (1) rewriting every `allOf`/`if-then` rule as a `oneOf` discriminated union so pydantic picks it up natively, (2) hand-writing companion `@model_validator` functions that subclass the generated classes, (3) doing nothing. We chose **(3)** explicitly.

Reasoning:

- The primary path users take to produce any SwarmKit artifact is a conversation with an authoring swarm (`swarmkit init` / `swarmkit author …`) — see `design/SwarmKit-Design-v0.6.md` §11–12. The Skill Authoring Swarm's Review Leader (§12.3) validates drafts against the schema before presenting them, so the conditional rules are caught at authoring time.
- The secondary path — hand-written YAML — hits `swarmkit validate` which uses jsonschema and catches every rule.
- The runtime load path calls `validate()` before pydantic model construction, so the gap is not reachable through any documented path.
- The only place the gap could bite is new runtime code that instantiates `SwarmKitX.model_validate(…)` on raw data without running `validate()` first — a reviewable, narrow contribution point.

Given that, paying the cost of (1) schema verbosity or (2) hand-written Python would optimise a path that nobody walks. The effort is better spent on **making validation tooling excellent** — human-readable error messages, rule citations, Authoring-Swarm Review Leader prompts that point at the offending YAML path. Tracked as task #23.

If the usage model ever changes (e.g. a codegen consumer that takes raw YAML and produces pydantic objects without going through `validate()`), revisit this decision — most likely by rewriting the rules as discriminated unions.

## Generated surface

```
packages/schema/python/src/swarmkit_schema/models/
├── __init__.py           # re-exports the 5 root models
├── topology.py           # SwarmKitTopology + nested types
├── skill.py              # SwarmKitSkill + nested types
├── archetype.py          # SwarmKitArchetype + nested types
├── workspace.py          # SwarmKitWorkspace + nested types
└── trigger.py            # SwarmKitTrigger + nested types
```

Public API (what runtime code uses):

```python
from swarmkit_schema.models import (
    SwarmKitTopology,
    SwarmKitSkill,
    SwarmKitArchetype,
    SwarmKitWorkspace,
    SwarmKitTrigger,
)
from swarmkit_schema import validate

data = yaml.safe_load(open("topology.yaml"))
validate("topology", data)                     # authoritative
topology = SwarmKitTopology.model_validate(data)  # typed access
# topology.agents.root.id is typed and IDE-navigable
```

## Tool choice

`datamodel-code-generator` — the established JSON Schema → pydantic generator in the Python ecosystem. Pydantic v2 output. Pinned loosely (`>=0.26`) because the generator itself is stable and backward-compatible in recent releases.

Codegen invocation is a single script: `scripts/codegen_pydantic.py`, wrapped by `just schema-codegen`. Notable flags:

- `--output-model-type pydantic_v2.BaseModel` — pydantic v2, not v1 or dataclasses.
- `--use-title-as-name` — top-level class of each schema takes its `title` (so root classes are `SwarmKitTopology` etc., not auto-generated names).
- `--use-standard-collections --use-union-operator` — modern typing syntax (`list[T]`, `str | None`).
- `--collapse-root-models` — simplifies nested `$ref` chains.
- `--enum-field-as-literal one` — string-valued single-enum fields become `Literal["value"]` rather than synthetic enum classes for `apiVersion`, `kind`, etc.
- `--allow-population-by-field-name` — allows code to construct models using Python attribute names (`class_`) while still accepting the original YAML field name (`class`) on input.
- `--disable-timestamp` — deterministic output for git diffs.

Each generated file starts with `# ruff: noqa` and `# mypy: ignore-errors` — we do not lint or typecheck generated output. The generator's choices are not meaningful signals about SwarmKit code quality.

## Drift protection

Two justfile targets:

- `just schema-codegen` — regenerate models from schemas. Run after any schema edit, per `docs/notes/schema-change-discipline.md` step 4.
- `just schema-codegen-check` — regenerate, then fail if git shows any diff. Runs in CI; a schema change without matching regenerated output breaks the build immediately.

## Test plan

- **Import test:** every root model imports from `swarmkit_schema.models`.
- **Valid-fixture test:** every fixture under `tests/fixtures/<kind>/` loads into its pydantic model. Round-trip via `model_dump(mode="json", exclude_none=True)` through yaml preserves the data.
- **Invalid-fixture split test:** pydantic catches structural errors (missing required fields the generator did see), but NOT `allOf`/`if-then` rules. Tests assert the known-limitation cases explicitly so regressions in either direction are caught.
- **Drift test:** `just schema-codegen-check` runs in CI; tests pass only if the checked-in generated files match what the generator would produce today.

## Demo plan

`just demo-codegen` — prints a typed object loaded from each fixture and a `model_dump_json(indent=2)` of the topology fixture so reviewers can see the generated shape in action.

## Open questions / follow-ups

- **Human-readable `swarmkit validate` errors (Task #23).** The real investment the if-then gap redirects us toward: rule citations, YAML path highlighting, remediation hints. Lands around M1 when the CLI validator is wired up.
- **Deeper nested-type ergonomics.** Some generated intermediate classes (`Model`, `Prompt`, `Iam`, etc.) have plain names that collide across modules if imported together. Not a problem at the public API (only root models are exported) but could be tightened with `--class-name-template`.
- **Runtime cost.** Codegen runs only in dev (not at runtime). Pydantic model instantiation is O(n) over object tree; the full round-trip for a typical topology is <1 ms.
