---
title: Skill schema v1
description: Four-category discriminator, composition rules, provenance, decision-category reasoning contract.
tags: [schema, skill, m0]
status: implemented
---

# Skill schema v1

**Scope:** `packages/schema`
**Design reference:** §6 (Skills — The Universal Extension Primitive), especially §6.2 (categories), §6.3 (anatomy), §6.4 (provenance), §6.5 (composition), §8.7 (IAM scopes), §18.3 (example).
**Status:** in review

## Goal

Promote the v0.6 skill sketch (§6.3) into a full JSON Schema v1 spec. Enforces the four-category discriminator, the implementation discriminated union, the provenance requirement, and the runtime-invoked input/output shape. Every skill example in the design doc (§6.3, §6.6, §18.3) round-trips cleanly.

## Non-goals

- Runtime dispatch semantics per category — the schema captures shape; the runtime implements semantics (M3 / M4).
- MCP server scaffolding schema — generated MCP servers are code, not schema artifacts (§8.8).
- Skill gap log format — operational data, not a declarative artifact (§12.1).
- Cross-skill version resolution — "skill A requires skill B v>=1.2" is a registry concern (M10), not a per-skill schema field.

## API shape

### Top-level structure

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: code-quality-review
  name: Code Quality Review
  description: |
    Evaluates a code diff for quality issues including SRP, error handling,
    and naming conventions. Returns pass/fail verdict with confidence score.
category: decision
inputs:
  diff:
    type: string
    required: true
  language:
    type: enum
    values: [python, typescript, go]
    required: true
outputs:
  verdict:
    type: enum
    values: [pass, fail]
  confidence:
    type: number
    range: [0, 1]
  reasoning:
    type: array
    items:
      type: object
      properties:
        criterion: { type: string }
        verdict: { type: string }
implementation:
  type: mcp_tool
  server: rynko_flow
  tool: validate_code_review_v2
iam:
  required_scopes: [repo:read]
constraints:
  max_latency_ms: 2000
  retry:
    attempts: 2
    backoff: exponential
  on_failure: escalate_to_human
provenance:
  authored_by: human
  authored_date: 2026-04-15
  version: 1.0.0
```

### Deliberate deviations from v0.6 inline examples

The §6.3 example shows skill fields at the top level (no `apiVersion`/`kind`/`metadata` wrapping). That's fine for inline documentation but inconsistent with topology (`kind: Topology`) and workspace (`kind: Workspace`). This schema **does** require `apiVersion` / `kind: Skill` / `metadata` because:

1. Skill files are standalone artifacts, stored under `skills/`, shared through a registry (M10). They need the same versioning contract as topologies.
2. `metadata.id` is the registry-lookup key; separating it from the skill's functional body keeps concerns clean.
3. Tooling (editor plugins, registry search, UI browser) relies on `kind` to distinguish artifact types at a glance.

The functional body (`category`, `inputs`, `outputs`, `implementation`, `iam`, `constraints`, `provenance`) sits at the top level alongside `metadata` — no unnecessary nesting. Documented as a design-level deviation.

### `category` — the discriminator

Required. Exactly one of (§6.2):

| Value | Purpose | Returns |
|---|---|---|
| `capability` | Give the agent a new ability | Output data |
| `decision` | Let the agent evaluate or judge | Verdict with confidence |
| `coordination` | Let the agent communicate or hand off | Task status |
| `persistence` | Let the agent remember or record | Write confirmation |

Categories differ in runtime semantics, not in schema shape. The schema enforces membership; the runtime applies per-category handling (e.g. decision skills produce judicial-pillar outputs, persistence skills go to the append-only media pillar).

### Decision-category output contract

Decision skills have a stricter output contract than the other categories. When `category: decision`, the schema requires `outputs.reasoning` to exist and to be `type: string`. Rationale: the judicial pillar (§8) writes every decision into the append-only audit log (§16.4), and "verdict + confidence" alone is low-value for a reviewer months later. A 2-3-sentence plain-language rationale is cheap to produce (the model already reasoned to reach the verdict) and invaluable for audits, post-hoc review, and skill-gap detection (§12.1).

Concretely every decision skill must declare at least:

```yaml
outputs:
  reasoning:
    type: string
    description: Two to three sentences justifying the verdict.
```

Additional structured detail (per-criterion breakdowns, panel vote records, vendor attestation IDs) can live in other fields with their own names — `criteria`, `panel_votes`, `run_id`. Only `reasoning` is reserved for the plain-language summary.

**Vendor-published skills** whose underlying API does not return a rationale must still comply — the skill wrapper synthesises one from the structured response. That's a small cost (~100 tokens per decision) for a qualitatively large gain (every audit event self-explains).

`verdict` and `confidence` are **strongly recommended** but not yet schema-enforced; may be promoted to required in a follow-up PR.

### `inputs` / `outputs`

A dictionary of field specs. Each field:

```yaml
<field-name>:
  type: string | number | integer | boolean | enum | object | array
  required: true | false           # inputs only; defaults false
  description: "free text"          # optional
  values: [v1, v2, ...]             # required when type=enum
  range: [min, max]                 # optional, only for number/integer
  items: <field spec>               # required when type=array
  properties: { name: <field spec> }  # optional when type=object
```

Rationale for a mini-DSL vs. embedding JSON Schema inline: skill authors are not JSON Schema experts. A purpose-built, small DSL is easier to write, read, and diff. The runtime translates to JSON Schema internally for validation.

### `implementation` — discriminated union

```yaml
# type=mcp_tool — the common case
implementation:
  type: mcp_tool
  server: rynko_flow           # server ID registered in workspace
  tool: validate_invoice_v3    # tool name on that server

# type=llm_prompt — single-LLM-call skill (e.g. judges, drafters)
implementation:
  type: llm_prompt
  model:                       # optional; inherits from agent.model
    provider: anthropic
    name: claude-sonnet-4-6
  prompt: |
    You are evaluating code against the following criteria...

# type=composed — composition of other skills (§6.5)
implementation:
  type: composed
  composes: [judge-correctness, judge-security, judge-style]
  strategy: parallel-consensus  # parallel-consensus | sequential | custom
```

The v1.0 `type` enum: `mcp_tool` | `llm_prompt` | `composed`. Extensible — future types (`python`, `webhook`, etc.) bump schema within v1 as additive changes.

### `iam`

```yaml
iam:
  required_scopes: [repo:read, github:write]
```

Scopes are strings matching `^[a-z][a-z0-9_-]*:[a-z][a-z0-9_*-]*$` (namespace `:` action, e.g. `repo:read`, `github:write`, `skills:activate`, `mcp_servers:*`). The schema enforces the pattern but not the vocabulary — scope names are an open set.

Reserved-for-human scopes from §8.7 (`skills:activate`, `mcp_servers:deploy`, `topologies:modify`, `iam:modify`) may appear in `required_scopes`, which means the skill can only be invoked by human identity. The runtime enforces this at `GovernanceProvider.evaluate_action` time; the schema just accepts the strings.

### `constraints`

```yaml
constraints:
  max_latency_ms: 2000                    # int, optional
  timeout_seconds: 30                     # int, optional
  retry:
    attempts: 2                           # int >= 0
    backoff: exponential | linear | none
  on_failure: escalate_to_human | fail | retry | fallback
```

All optional. Sensible defaults applied at load time (no retry, no timeout beyond provider defaults, `on_failure: fail`).

### `provenance` — required on every skill (§6.4)

```yaml
provenance:
  authored_by: human | authored_by_swarm | derived_from_template | imported_from_registry | vendor_published
  authored_date: "2026-04-15"         # optional, YYYY-MM-DD
  version: "1.0.0"                     # required, semver
  registry: community/analytics        # when authored_by=imported_from_registry
  vendor: rynko                        # when authored_by=vendor_published
```

The runtime applies different trust defaults by value — the schema just records it.

## What's not in the schema

- **Discovery metadata.** Tags, keywords, example usage. Useful for a registry (M10) but not needed for runtime validation. Can be added in a later schema minor.
- **Cost estimates.** Token budget per invocation — depends on the model used, not the skill definition.
- **Deprecation markers.** Belongs in registry metadata, not the skill file.
- **Arbitrary validation hooks.** The implementation already runs; anti-patterns are caught by the skill-authoring Review Leader (§12.3), not by schema rules.

## Test plan

Following `docs/notes/schema-change-discipline.md`:

- **Valid fixtures** under `packages/schema/tests/fixtures/skill/`:
  - `minimal-capability.yaml` — smallest capability skill (mcp_tool, no inputs/outputs/constraints)
  - `decision-mcp.yaml` — the §6.3 `code-quality-review` example verbatim
  - `decision-llm.yaml` — `llm_prompt` implementation
  - `composed-panel.yaml` — `composed` implementation (§6.5)
  - `coordination-handoff.yaml` — peer-handoff skill
  - `persistence-audit.yaml` — audit-log-write skill
  - `vendor-published.yaml` — provenance variant exercising `vendor` field
  - `from-design-doc-invoice.yaml` — the §18.3 invoice-validation example adapted
- **Invalid fixtures** under `packages/schema/tests/fixtures/skill-invalid/`:
  - `missing-category.yaml`
  - `bad-category.yaml` — category not in the 4-value enum
  - `missing-provenance.yaml`
  - `bad-semver.yaml` — provenance.version not semver
  - `impl-missing-discriminator.yaml` — implementation without `type`
  - `mcp-tool-missing-server.yaml` — mcp_tool impl without `server`
  - `composed-empty.yaml` — `composes: []`
  - `bad-scope-pattern.yaml` — scope like `REPO:read` (uppercase)
- **Python test:** extends `test_schemas.py` with parametrised valid + invalid skill cases.
- **TS test:** matching additions to `index.test.ts`.

## Demo plan

`just demo-skill-schema` — parallel to `demo-topology-schema`. Loads every fixture in Python + TS, prints pass/fail. Shipped with this PR.

The aggregate `just demo-schema` target (Task #16) will combine all five per-schema demos once the last schema PR lands.

## Open questions

- **`apiVersion` / `kind` vs. top-level id.** Chose full envelope for consistency with topology and workspace. Design doc examples omit it; that's a documentation simplification, not a schema constraint. Flag for review.
- **`type: composed` and circular refs.** A composed skill references other skills. Circular composition (A composes B, B composes A) is schematically allowed but runtime-invalid. Detection happens at topology-load time in the resolver, not in the schema. Documented here, enforced in M1.
- **`implementation.type` enum extensibility.** v1.0 lists three types. A future `python` type (for hand-coded in-process skills) and `webhook` type (for HTTP endpoints) are plausible. Schema growth is additive within v1; new types need only add to the `oneOf` list.
- **Abstract-skill placeholders (§6.6 edge case).** "An archetype can declare 'this agent needs a skill of category X with capability Y' without specifying which concrete skill." This belongs in the **archetype** schema, not the skill schema. Picked up in `archetype-schema-v1.md`.

## Follow-ups (separate PRs)

- Archetype schema v1 (Task #11) — references skill IDs; builds on this PR.
- Workspace schema v1 (Task #12) — MCP server registry for the `server:` field resolution lives here.
- `just demo-schema` meta-target (Task #16).
