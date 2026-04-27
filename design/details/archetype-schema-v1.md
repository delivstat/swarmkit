---
title: Archetype schema v1
description: Reusable agent configuration with abstract-skill placeholder support.
tags: [schema, archetype, m0]
status: implemented
---

# Archetype schema v1

**Scope:** `packages/schema`
**Design reference:** §6.6 (skills vs archetypes), §13 (Archetype Library), §6.4 (provenance model reused).
**Status:** in review

## Goal

Promote the v0.6 archetype sketch (§6.6, §13) into a full JSON Schema v1 spec. Captures the three-layer composition hierarchy from §6.6: **topologies instantiate archetypes; archetypes reference skills**. The schema locks down archetype defaults, the abstract-skill placeholder pattern (§6.6 edge case), and archetype provenance (§13 addition from v0.3).

## Non-goals

- Runtime archetype merge semantics (how topology overrides combine with archetype defaults) — covered in M1 (topology resolver).
- The archetype **catalogue** — the ~15 v1.0 archetypes from §13.1 — lands as individual reference artifacts in M6/M7, not as part of this schema PR.
- Versioning migrations — additive changes within `swael/v1`; breaking changes bump to `v2`.

## API shape

### Top-level structure

```yaml
apiVersion: swael/v1
kind: Archetype
metadata:
  id: code-review-worker
  name: Code Review Worker
  description: |
    A worker specialised in evaluating code diffs against quality rubrics.
    Defaults to Claude Sonnet, inherits repo:read. Used across Code Review
    Swarm and any derived review topology.
role: worker
defaults:
  model:
    provider: anthropic
    name: claude-sonnet-4-6
    temperature: 0.2
  prompt:
    system: |
      You are a senior code reviewer specialising in Python and TypeScript.
      Focus on correctness and maintainability.
  skills:
    - github-repo-read
    - eslint-analyse
    - code-quality-review
    - audit-log-write
  iam:
    base_scope: [repo:read]
    elevated_scopes: []
provenance:
  authored_by: human
  authored_date: "2026-04-15"
  version: 1.2.0
```

### `role` — matches topology agent roles

Required. Exactly one of `root` | `leader` | `worker`. An archetype can only be instantiated at a matching agent role in a topology (enforced at load time — the topology schema already declares the agent's role).

### `defaults` — what gets merged into an instantiated agent

Every field in `defaults` is optional individually; the block itself is required (can be empty only if the archetype is purely a typed placeholder — unusual, but not rejected).

Field shape mirrors the **topology agent** schema definitions, re-used verbatim where possible:

| Field | Matches topology's... | Purpose |
|---|---|---|
| `model` | `agent.model` | Default model config; topology agents may override entirely or field-by-field. |
| `prompt` | `agent.prompt` | Default system prompt template. |
| `skills` | `agent.skills` | Default skill set — each entry is either a concrete skill ID (string) or an abstract-skill placeholder (object). See below. |
| `iam` | `agent.iam` | `base_scope` + `elevated_scopes` defaults. Topology agents may add additional scopes through `skills_additional`-style pattern (covered in topology resolver, M1). |

### Abstract-skill placeholders (§6.6 edge case)

An archetype can declare "this agent needs a skill of category X with capability Y" without specifying the concrete skill. The topology fills in the concrete ID at instantiation. This makes archetypes reusable across different validation backends (different MCP providers, for instance) without per-backend archetype forks.

```yaml
defaults:
  skills:
    - audit-log-write                  # concrete: skill ID string
    - abstract:                        # abstract: placeholder object
        category: decision
        capability: content_review
```

Discriminator: a **string** entry is a concrete skill ID; an **object** entry with an `abstract` key is a placeholder. The abstract object has required `category` (four-value enum matching skill.schema.json) and optional `capability` (free-text tag, matched by the topology resolver against skill metadata).

The topology resolver (M1) fails to load a topology if an abstract placeholder remains unresolved.

### `provenance` — reused from skill schema (§6.4 / §13)

Same shape, same values, same trust implications. Archetypes authored by swarm require human review before being used to instantiate production agents (§13 "archetypes carry provenance on the same basis as skills").

## Deliberate deviations from v0.6 inline examples

Same pattern as skill-schema-v1: the §6.6 example shows archetype fields at the top level without `apiVersion` / `kind` / `metadata` wrapping. This schema **does** require them for the same reasons:

1. Archetypes are standalone artifacts under `archetypes/`, registry-shareable (M10).
2. `metadata.id` is the registry-lookup key — topologies reference archetypes by this ID.
3. Tooling distinguishes artifact types by `kind`.

The functional body (`role`, `defaults`, `provenance`) sits at the top level alongside `metadata`.

## What's not in the schema

- **Constraint propagation rules** — how an archetype's `iam.base_scope` combines with a topology agent's override is runtime behaviour, not schema shape. Covered in `design/details/topology-resolver.md` (M1).
- **Archetype inheritance** — "archetype B extends archetype A" is tempting but out of scope for v1. Composition through reference (topology picks the right archetype) is sufficient.
- **Capability tags for abstract placeholders** — `capability: content_review` is a free-text tag matched at topology-load time. A registry-wide tag vocabulary is a registry concern (M10).

## Test plan

Following `docs/notes/schema-change-discipline.md`:

- **Valid fixtures** under `packages/schema/tests/fixtures/archetype/`:
  - `minimal-worker.yaml` — smallest valid archetype (role + provenance; empty `defaults: {}`).
  - `code-review-worker.yaml` — the §6.6 example verbatim.
  - `supervisor-leader.yaml` — the canonical leader archetype from §13.1 catalogue.
  - `abstract-skill.yaml` — archetype declaring a placeholder skill (§6.6 edge case).
  - `swarm-authored.yaml` — provenance variant exercising `authored_by_swarm`.
- **Invalid fixtures** under `packages/schema/tests/fixtures/archetype-invalid/`:
  - `missing-role.yaml`
  - `bad-role.yaml` — role not in the 3-value enum
  - `missing-provenance.yaml`
  - `bad-id-pattern.yaml` — uppercase id
  - `abstract-missing-category.yaml` — abstract placeholder without required `category`
  - `abstract-bad-category.yaml` — abstract placeholder's category not in the 4-value skill enum
  - `skill-entry-wrong-shape.yaml` — skill entry that's neither a string nor an object-with-`abstract`
- **Python test:** extends `test_schemas.py` with parametrised valid + invalid archetype cases.
- **TS test:** adds `describeFixtures("archetype", ...)` call; no code to write because the TS test already generalised.

## Demo plan

`just demo-archetype-schema` — cross-language demo via the existing `_demo-schema` recipe. No new scripts.

## Open questions

- **Empty `defaults`.** Is an archetype with no defaults meaningful? Arguably yes — a "typed tag" archetype (just role + provenance) could mark agents as belonging to a class. Schema allows it; convention discourages it. Documented as non-idiomatic.
- **Capability-tag vocabulary.** Free-text for now. If abusive drift appears during M6/M7 reference-artifact work, we formalise.
- **Archetype "extends" keyword** — rejected for v1; deferred to community feedback post-v1.0.

## Follow-ups (separate PRs)

- Workspace schema v1 (Task #12).
- Trigger schema v1 (Task #13).
- Topology resolver (M1) — where archetype merge actually happens.
- Reference archetype catalogue (M6 / M7) — the fifteen v1.0 archetypes from §13.1.
