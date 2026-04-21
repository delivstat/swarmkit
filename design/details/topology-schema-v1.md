---
title: Topology schema v1
description: Full v1 spec for the topology artifact — apiVersion, kind, metadata, runtime, agents (recursive tree), artifacts.
tags: [schema, topology, m0]
status: implemented
---

# Topology schema v1

**Scope:** `packages/schema`
**Design reference:** §10 (Topology Schema, high-level), §5.2 (agent hierarchy), §5.3 (communication patterns), §6.1 (skill references), §9.3 (workspace structure)
**Status:** in review

## Goal

Promote the v0.6 topology sketch (§10) into a full JSON Schema v1 spec, tight enough that every example artifact in the design doc validates and loose enough to evolve without breaking changes through v1.x.

## Non-goals

- Skill schema details (`skill.schema.json`) — covered separately.
- Archetype schema details — covered separately.
- Pydantic / TS codegen — a follow-up PR.
- Runtime semantics — the schema defines shape, not execution behaviour.
- Inline skill definitions. Per §6.1 skills are always referenced by ID; the topology schema forbids inline skill bodies.

## API shape

### Top-level structure

```yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: code-review-swarm
  version: 1.0.0
  description: |
    The canonical Cisco-style multi-leader review flow.
runtime:
  mode: persistent                # one-shot | persistent | scheduled
  max_concurrent_tasks: 5
  task_timeout_seconds: 300
  checkpointing:
    storage: sqlite               # sqlite | postgres
agents:
  root: { ... }                   # see "Agent tree" below
artifacts:
  knowledge_bases: [ ... ]
  review_queues: [ ... ]
  audit:
    level: detailed               # minimal | standard | detailed
    storage: sqlite
    retention_days: 90
  skill_gap_logging:
    enabled: true
    surface_threshold: 5
```

### Agent tree (nested `children`, recursive)

The design doc §5.2 says "each agent has exactly one parent. No diamond inheritance. This keeps the topology a tree." The schema enforces this structurally by using nested `children` rather than flat `parent` references — a node can only appear once in the tree.

```yaml
agents:
  root:
    id: root-orchestrator
    role: root
    archetype: supervisor-root
    model:
      provider: anthropic
      name: claude-opus-4-7
      temperature: 0.2
    prompt:
      system: |
        You coordinate the review swarm across engineering, QA, and ops.
    skills: [a2a-handoff-peer, audit-log-write]
    iam:
      base_scope: [topology:read]
    children:
      - id: engineering-leader
        role: leader
        archetype: judge-and-handoff-leader
        children:
          - id: code-reviewer
            role: worker
            archetype: code-review-worker
            skills_additional: [security-specific-review]
      - id: qa-leader
        role: leader
        archetype: supervisor-leader
        children:
          - id: test-runner
            role: worker
            archetype: mcp-caller-worker
```

Constraints enforced by the schema:

- `agents.root` is exactly one object with `role: root`.
- `role: root` may appear only at `agents.root`.
- `children` is optional for every agent. Workers typically have no `children`; leaders typically do.
- Every `id` is globally unique within the topology (runtime-enforced, not schema-enforced — JSON Schema can't express tree-wide uniqueness cleanly; the runtime resolver does this check).
- `id` matches `^[a-z][a-z0-9-]*$` (per repo convention, already in place).
- An agent with `archetype` MAY override any field from the archetype's defaults. Omitting a field inherits from the archetype.

### Agent fields

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Unique within topology. |
| `role` | yes | `root` \| `leader` \| `worker`. |
| `archetype` | no | If set, all other fields are optional (archetype provides defaults). If unset, `model` and `prompt` are required. |
| `model` | conditional | Inherited from archetype if unset. |
| `prompt` | conditional | Inherited from archetype if unset. |
| `skills` | no | List of skill IDs. Replaces archetype's skill list when present. |
| `skills_additional` | no | List of skill IDs merged onto the archetype's default skills (per §6.6 example). |
| `iam` | no | `{ base_scope: [string], elevated_scopes: [string] }`. Inherited + merged with archetype. |
| `children` | no | Array of agent definitions (recursive). |

### Model block

```yaml
model:
  provider: anthropic             # anthropic | openai | google | azure | local | custom
  name: claude-sonnet-4-6
  temperature: 0.2                # 0.0–2.0
  max_tokens: 4096                # optional
  # additional provider-specific fields allowed (additionalProperties: true)
```

### Prompt block

```yaml
prompt:
  system: |
    ...string or template...
  persona: analytical             # optional free-text label
  # additional fields allowed for future templating mechanisms
```

### Runtime block

```yaml
runtime:
  mode: persistent                # one-shot | persistent | scheduled
  max_concurrent_tasks: 5         # >= 1
  task_timeout_seconds: 300       # >= 1
  checkpointing:
    storage: sqlite               # sqlite | postgres
    # Additional config fields allowed; resolved at runtime.
```

All fields optional; sensible defaults applied at load time.

### Artifacts block

Unchanged from the v0.6 sketch — `knowledge_bases`, `review_queues`, `audit`, `skill_gap_logging`. Each is optional. Detailed schemas for KB/review-queue items are deferred until M4 (persistence skills).

## What's not in the schema

- **Communication patterns.** §5.3 categorises hierarchical / direct / guarded / A2A. Per §6.1 these are all skills. The topology doesn't declare channels; it declares skills. The runtime infers the communication graph from agent hierarchy plus coordination-skill invocations.
- **Workspace-level config.** Identity provider, governance provider, storage backends — those live in `workspace.yaml` (covered by `workspace-schema-v1.md`).
- **Schedules and triggers.** Per §5.4 these are workspace-level, not topology-level. `trigger.schema.json` covers them.

## Test plan

- **Unit (Python):** `test_schemas.py` gets a parametrised test that loads every YAML under `packages/schema/tests/fixtures/topology/` and validates. Fixtures include:
  - `minimal.yaml` — the smallest valid topology (root only).
  - `two-level.yaml` — root + one leader + one worker.
  - `nested.yaml` — root + leader + sub-leader + worker (tests recursion).
  - `with-artifacts.yaml` — all artifact blocks populated.
  - `from-design-doc.yaml` — the §10.1 example verbatim (adapted to `children` nesting).
- **Unit (TS):** matching vitest cases reading the same fixtures.
- **Negative cases:** a second parametrised test loads fixtures under `tests/fixtures/topology-invalid/` and asserts validation fails:
  - missing `apiVersion`
  - wrong `kind`
  - `role: root` on a non-root agent
  - duplicate `id` (covered by runtime resolver test, not schema — document the gap)
  - invalid `id` pattern (uppercase, starts with digit)
  - `children` under a worker archetype (allowed by schema — pure-tree check only; runtime does policy)

## Demo plan

- `just demo-topology-schema` (added in this PR) loads every `tests/fixtures/topology/*.yaml` and prints a green table:

  ```
  ✓ minimal.yaml
  ✓ two-level.yaml
  ✓ nested.yaml
  ✓ with-artifacts.yaml
  ✓ from-design-doc.yaml
  ```

- Paste the transcript in the PR body.

## Open questions

- **`children` vs flat `parent`.** Chose `children` per §5.2 ("tree") and because the UI composer's Structure View (§15.2) is org-chart shaped. Flag to revisit if authoring experience suffers.
- **`additionalProperties: false` vs `true` at the top level.** Top-level is `false` (strict). Provider-specific blocks (`model`, `prompt`) are `true` to allow extension without schema bumps. Document the extensibility boundary.
- **Versioning migrations.** §10 says `apiVersion: swarmkit/v1` is the contract. When the schema evolves within v1, do we add minor-version fields? Yes — additive changes only; breaking changes get `v2`. Covered by CLAUDE.md "schema versioning."
- **Top-level `description`.** Sketched in v0.6. Keeping it optional. Long form belongs in docs, not in the artifact.

## Follow-ups (separate PRs)

- Pydantic model codegen from this schema (Task #14).
- TS type codegen (Task #15).
- Apply the same level of rigour to `skill`, `archetype`, `workspace`, `trigger` (Tasks #10–#13).
- `just demo-schema` meta-target combining all five demos (Task #16).
