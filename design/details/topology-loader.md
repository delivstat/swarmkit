---
title: Topology loader and resolver
description: M1 core — take a workspace directory, discover artifacts, validate, resolve archetype + skill references, and produce a frozen typed ResolvedTopology the compiler can consume.
tags: [runtime, resolver, m1]
status: draft
---

# Topology loader and resolver

**Scope:** `packages/runtime/src/swarmkit_runtime/`
**Design reference:** `design/SwarmKit-Design-v0.6.md` §10 (topology schema), §14.3 steps 1–3 (load/validate/resolve phase of the runtime pipeline), §9.3 (workspace structure).
**Status:** draft — M1 core design. Implementation PRs follow this note.

## Goal

Given a workspace directory on disk (`workspace.yaml`, `topologies/*.yaml`, `archetypes/*.yaml`, `skills/*.yaml`, optional `triggers/`), produce a frozen typed `ResolvedTopology` that the M3 LangGraph compiler can consume. This is the bridge between "declarative YAML on disk" and "runtime-ready typed data structure."

Four phases, in order:

1. **Discovery** — walk the workspace directory, find every artifact file, parse YAML.
2. **Validation** — run `swarmkit_schema.validate()` (jsonschema) on each artifact.
3. **Resolution** — merge archetype defaults into agent definitions; link skill references; expand abstract-skill placeholders.
4. **Model construction** — produce frozen `ResolvedTopology` pydantic models.

## Non-goals

- **Execution.** The resolver produces data; the M3 LangGraph compiler executes.
- **Governance enforcement.** `GovernanceProvider` (M2) is not invoked during resolution. Resolution is declarative; policy evaluation happens at runtime dispatch time.
- **Model invocation / MCP calls.** Not part of resolution; those are M3–M5 runtime concerns.
- **Hot reload.** Workspaces are loaded once per process in v1.0. File-watcher-driven reload is a future enhancement.
- **Partial workspaces.** A workspace either resolves or it doesn't. We do not ship "best-effort" mode.

## The four phases in detail

### Phase 1 — Discovery

The workspace directory structure (§9.3):

```
<workspace>/
├── workspace.yaml                    # required; exactly one
├── topologies/*.yaml                 # one or more
├── archetypes/*.yaml                 # zero or more
├── skills/*.yaml                     # zero or more
├── triggers/*.yaml                   # zero or more (schedules also land here)
├── schedules/*.yaml                  # zero or more (convention-only; also kind: Trigger)
├── knowledge_bases/                  # opaque to the resolver
├── review_queues/                    # opaque to the resolver
└── .swarmkit/                        # opaque to the resolver (runtime state)
```

Discovery reads from the four directories the resolver cares about (`topologies/`, `archetypes/`, `skills/`, and `triggers/` + `schedules/` merged). File extension: `.yaml` and `.yml`. Recurses one level (subdirectories allowed for grouping but not required). Ignores hidden files.

A single discovery pass produces a flat list of `(path, kind, raw_dict)` tuples plus the parsed `workspace.yaml`. YAML parse errors fail discovery with the offending file path + line.

### Phase 2 — Validation

For each discovered artifact, run `swarmkit_schema.validate(kind, raw_dict)`. This is the authoritative layer — `allOf`/`if-then` rules and everything else the JSON Schema specifies are caught here (see `design/details/pydantic-codegen.md` for why the pydantic layer doesn't cover these).

Validation errors don't short-circuit. We collect every error across every artifact and fail with the aggregate list. A workspace with three broken skills and one broken topology produces one error report listing all four, not four separate runs.

`ValidationError` at this phase is raw jsonschema output. Task #23 (human-readable errors) wraps this into a user-facing report; the wrapper receives the structured `(artifact-path, json-pointer, message)` tuples.

### Phase 3 — Resolution

The interesting phase. Three sub-steps, in order:

#### 3a. Skill resolution

For every skill file, construct the pydantic `SwarmKitSkill` model. Build a registry `{skill_id: ResolvedSkill}`. Skill IDs must be globally unique within the workspace; duplicates fail.

For skills with `implementation.type: composed`, verify every ID in `composes` exists in the registry. Composed skills are not inlined at this stage — they remain as references. Cycle detection runs here: a composed skill that (directly or transitively) references itself fails resolution with the cycle path listed.

#### 3b. Archetype resolution

For every archetype file, construct `SwarmKitArchetype`. Build a registry `{archetype_id: ResolvedArchetype}`. Abstract-skill placeholders in archetype defaults are **kept abstract** at this stage — they're resolved per-agent when the topology merges archetype defaults.

For concrete skill references in archetype defaults, verify they exist in the skill registry.

#### 3c. Topology resolution

For every topology file, walk the agent tree. For each agent:

1. If `archetype` is set, look up the archetype in the registry. Merge `defaults` into the agent in this precedence order (lowest to highest):
   1. Archetype `defaults.model`
   2. Archetype `defaults.prompt`
   3. Archetype `defaults.skills` (list)
   4. Archetype `defaults.iam`
   5. Agent's own `model` (overrides archetype)
   6. Agent's own `prompt` (overrides archetype)
   7. Agent's own `skills` (**replaces** archetype's list, per §6.6)
   8. Agent's `skills_additional` (**merged onto** the resulting skills list)
   9. Agent's own `iam`

   Dict fields merge shallow (overrides win); list fields replace unless `skills_additional` pattern applies.

2. Resolve each skill reference in the merged skill list:
   - String entries → look up in the skill registry; fail if not found.
   - Abstract placeholders (from archetype defaults that weren't overridden) → find a concrete skill in the registry whose `category` matches and whose `metadata.id` or `tags` match the `capability` tag. Matching rules:
     - Exact match on `capability` field if the skill declares one (future addition).
     - For v1.0, match by: `category` + `capability` tag being a substring of the skill's `metadata.id`.
     - If multiple matches or zero matches, fail with both candidates listed.

3. Produce a `ResolvedAgent` — inlined model/prompt/iam, list of `ResolvedSkill` references (not IDs), list of `ResolvedAgent` children (recursion).

Agents' `id` must be globally unique within the topology (not just within their parent subtree).

### Phase 4 — Model construction

```python
@dataclass(frozen=True)
class ResolvedWorkspace:
    raw: SwarmKitWorkspace                         # original pydantic model
    topologies: Mapping[str, ResolvedTopology]     # keyed by topology id
    skills_registry: Mapping[str, ResolvedSkill]   # all skills in the workspace
    archetypes_registry: Mapping[str, ResolvedArchetype]
    triggers: Sequence[ResolvedTrigger]

@dataclass(frozen=True)
class ResolvedTopology:
    metadata: TopologyMetadata
    runtime: TopologyRuntime
    root: ResolvedAgent
    artifacts: TopologyArtifacts

@dataclass(frozen=True)
class ResolvedAgent:
    id: str
    role: Literal["root", "leader", "worker"]
    model: ResolvedModel           # merged from archetype + agent override
    prompt: ResolvedPrompt
    skills: Sequence[ResolvedSkill]  # fully resolved, not IDs
    iam: ResolvedIam
    children: Sequence["ResolvedAgent"]
    source_archetype: str | None   # provenance, for debugging
    source_path: Path              # which .yaml file the agent came from

@dataclass(frozen=True)
class ResolvedSkill:
    id: str
    raw: SwarmKitSkill             # full pydantic model
    resolves_to: "ResolvedSkill" | None  # set for composed skills after resolution
    source_path: Path
```

All dataclasses are frozen. The resolver produces them once; downstream consumers read only.

## Error model

Every error the resolver can emit is one of:

```python
@dataclass(frozen=True)
class ResolutionError:
    code: str              # machine-readable: "schema.required-field", "skill.unknown-id", ...
    message: str           # short sentence
    artifact_path: Path    # which .yaml file
    yaml_pointer: str      # JSON-pointer into the YAML, e.g. "/agents/root/skills/2"
    rule: str | None       # schema rule citation, if applicable
    suggestion: str | None # remediation hint
    related: Sequence["ResolutionError"]  # for aggregate errors (e.g. "also affected:")
```

Resolution fails with `ResolutionErrors(errors=[...])` — the plural — carrying the full list. Downstream code (the CLI, the authoring swarms' Review Leader) turns these into human-readable output (task #23).

The important property: **every error has a YAML pointer and a suggestion.** The suggestion is a one-line "try this" that a user can act on without reading the design doc. Examples:

- `skill.unknown-id`: "Skill 'code-quality-review' referenced by agent 'engineering-leader' is not defined in this workspace. Add a skill file at `skills/code-quality-review.yaml`, or reference an existing skill from `skills/`."
- `archetype.circular-skill`: "Composed skill 'panel-judge' composes 'panel-judge' (transitively via 'sub-judge'). Composition cycles are not allowed."
- `agent.duplicate-id`: "Agent ID 'reviewer' appears twice in this topology (first at `topology.yaml:24`, then at `topology.yaml:38`). Agent IDs must be unique within a topology."

## Edge cases and decisions

### Abstract-skill placeholders — matching rules

The §6.6 edge case: an archetype can declare `abstract: { category, capability? }` instead of a concrete skill ID. Resolution binds this to a concrete skill at topology-load time.

**Decision for v1.0**: match by exact `category` + `capability` tag substring on skill ID. This is coarse but deterministic. Skill authors who want to be discoverable via a capability tag embed it in their skill ID (e.g. `content-review-rubric`). A richer matching vocabulary lands in a later minor.

**Ambiguity handling**: if multiple skills match, resolution fails with all candidates listed — users must either rename skills or use concrete references. We don't pick arbitrarily.

**No matches**: fail with the abstract requirement restated and a suggestion to either add a matching skill or use a concrete reference.

### Workspace-level skill / archetype sharing

Skills and archetypes live at the workspace level, not the topology level. All topologies in a workspace share the same registries. This matches §6.6 ("skills exist independently of agents") and §13 ("archetypes are shared across topologies").

If the user wants topology-scoped overrides, that's a topology concern (override specific skills per-agent). We do not support "this topology sees only these skills."

### Cross-topology references

Topologies cannot directly reference each other. A trigger can target multiple topologies (§5.4) but topologies don't embed each other. This keeps resolution acyclic.

### `workspace.yaml` optional fields

Most fields on `workspace.yaml` (governance, identity, model_providers, storage, mcp_servers, credentials) are consumed by the **runtime** (M2+), not by the resolver. The resolver only reads the `metadata`, `organisation`, `team` blocks to populate `ResolvedWorkspace.raw`. Everything else passes through unchanged.

This means a workspace with a broken `governance` block still resolves — the resolution errors it produces, if any, come only from topology/skill/archetype issues. Runtime failures (missing credentials, unknown AGT provider) surface at runtime, not at resolution.

### Deterministic output

Given the same workspace on disk, resolution produces byte-identical `ResolvedWorkspace` output. Ordering of registries is sorted-by-id. Ordering of children follows YAML document order. Needed for reproducibility, for `swarmkit eject` (M9) to emit stable code, and for diff-friendly logging.

## API shape

```python
from swarmkit_runtime.resolver import resolve_workspace, ResolutionErrors

try:
    workspace = resolve_workspace(Path("./my-workspace"))
except ResolutionErrors as exc:
    for err in exc.errors:
        print(f"{err.artifact_path}: {err.code} — {err.message}")
        if err.suggestion:
            print(f"  suggest: {err.suggestion}")

topology = workspace.topologies["code-review-swarm"]
for skill in topology.root.skills:
    ...
```

`resolve_workspace` is the single entry point. Both the CLI (`swarmkit validate`) and the runtime (`swarmkit run`, `swarmkit serve`) go through it.

## Module layout

New and extended modules under `packages/runtime/src/swarmkit_runtime/`:

| Module | Responsibility |
|---|---|
| `workspace/` (new) | `discover()` — walks a workspace directory, returns `(path, kind, raw_dict)` list. |
| `resolver/` (new) | `resolve_workspace()` — orchestrates phases 2–4. Emits `ResolvedWorkspace`. |
| `topology/` (exists; extend) | Agent-tree traversal, archetype merging, skill linking. |
| `skills/` (exists; extend) | Skill registry, composed-skill cycle detection, abstract-skill matching. |
| `archetypes/` (exists; extend) | Archetype registry, archetype-to-agent merge rules. |
| `errors/` (new) | `ResolutionError`, `ResolutionErrors`, helper constructors. |

The existing stubs (`cli/`, `governance/`, `langgraph_compiler/`, `mcp/`, `audit/`) stay as-is for M1; they're not in scope here.

## Test plan

Following the usability-first checklist (`docs/notes/usability-first.md`) and the schema-change discipline where relevant:

- **Fixture workspaces** under `packages/runtime/tests/fixtures/workspaces/`:
  - `minimal/` — `workspace.yaml` + one topology with root-only, no skills, no archetypes.
  - `with-archetypes/` — exercises archetype merge + override precedence.
  - `with-skills/` — exercises skill resolution (concrete refs, composed skills).
  - `with-abstract-skills/` — exercises the §6.6 placeholder pattern.
  - `nested-agents/` — deep children, tests recursion.
  - `multi-topology/` — three topologies sharing one skills directory.
- **Invalid fixtures** under `packages/runtime/tests/fixtures/workspaces-invalid/`:
  - `unknown-skill/` — topology references a skill ID that isn't defined.
  - `unknown-archetype/` — topology references an archetype ID that isn't defined.
  - `composed-cycle/` — skill A composes B composes A.
  - `duplicate-agent-id/` — two agents with the same id.
  - `abstract-no-match/` — abstract placeholder matches nothing.
  - `abstract-ambiguous/` — abstract placeholder matches multiple skills.
  - `schema-violation/` — a topology with a structural schema violation (should fail at phase 2, not phase 3).
- **Unit tests** for each resolver module (archetype merge precedence, skill registry, composed-skill cycle detection).
- **Integration tests** loading each fixture workspace end-to-end, asserting `ResolvedWorkspace` shape or the exact `ResolutionErrors` list.
- **Determinism test**: resolve the same workspace twice, assert byte-identical output.

## Demo plan

`just demo-resolver` — loads every valid fixture workspace, prints the resolved agent tree for each topology. For invalid fixtures, prints the `ResolutionErrors` list. One command, exercises every code path.

The **M1 exit demo** (per the implementation plan) is `swarmkit validate examples/hello-swarm/workspace/` on a real example workspace that doesn't yet exist in the repo. That example workspace is its own task — `feat(examples): hello-swarm workspace` — landing as part of M1's last PR.

## PR breakdown

Rather than one mega-PR, M1 lands as a sequence:

| # | Content | Depends on |
|---|---|---|
| M1.1 | This design note | — |
| M1.2 | `workspace/` — directory discovery + YAML parsing | M1.1 |
| M1.3 | `resolver/` — phase-2 validation + aggregate error collection + `ResolutionError` / `ResolutionErrors` types | M1.2 |
| M1.4 | `skills/` + `archetypes/` resolvers — registries, composed-skill cycle detection, archetype-merge precedence | M1.3 |
| M1.5 | `topology/` resolver — agent-tree walk, archetype merge application, abstract-skill placeholder matching, `ResolvedTopology` / `ResolvedAgent` construction | M1.4 |
| M1.6 | `swarmkit validate <path>` CLI + Typer wiring + exit-code behaviour | M1.5 |
| M1.7 | Task #23 — human-readable error rendering | M1.6, separate design note |
| M1.8 | Task #24 — `swarmkit knowledge-pack` CLI | M1.5 or later, separate design note |
| M1.9 | `hello-swarm` example workspace under `examples/` + `just demo-resolver` | M1.5 |

Each implementation PR is small and independently reviewable. The design note (this file) is the shared context all of them reference.

## Open questions

- **File encoding**: assume UTF-8. Anything else fails at YAML parse. Not worth configuring.
- **Symlinks inside a workspace**: follow them. Don't special-case. If a user wants to share artifacts across workspaces they can symlink.
- **Artifacts in subdirectories**: recurse one level. Deeper nesting is rejected with a clear error — the §9.3 structure is flat under each category.
- **YAML anchors / merges**: allowed; `yaml.safe_load` handles them. The resolved data structure has no trace of the YAML-level anchoring.
- **File-based caching**: out of scope for M1. Resolution runs on every CLI invocation; performance budget for v1.0 is "seconds on a 100-artifact workspace." If that breaks we revisit.

## Follow-ups

- **Task #23** (human-readable errors) — separate design note, M1-blocking.
- **Task #24** (knowledge-pack) — separate design note, M1 scope.
- **Example workspace** (`examples/hello-swarm/`) — lands with M1.9.
- **M3 compiler** — consumes `ResolvedWorkspace` as input. Design note lives there.
