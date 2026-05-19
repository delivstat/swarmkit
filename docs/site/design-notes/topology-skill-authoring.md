---
title: Skill Authoring Swarm — multi-agent topology for creating and editing swarm artifacts
description: A multi-agent swarm that creates new skills/topologies/archetypes and edits existing ones through conversation. Demonstrates SwarmKit dogfooding — the framework builds itself.
tags: [topology, reference, authoring, m7]
status: proposed
---

# Skill Authoring Swarm

## Goal

Replace the single-agent authoring flow (`swarmkit author`) with a
multi-agent swarm that produces higher-quality artifacts through
specialization. Each agent has one job — conversation, schema drafting,
existing-skill search, design-grounded validation, test generation,
file publication. An external validator checks every output against
the design invariants before publication.

Additionally: the swarm can **edit existing configurations**, not just
create new ones. Users can conversationally modify a running swarm
based on their experience — "my code review swarm misses dependency
vulnerabilities, add a skill for that."

This is SwarmKit dogfooding — the framework uses its own multi-agent
solution to build itself.

## Why multi-agent instead of improving the single agent

The single agent (M3.5) works for simple cases but has structural
limitations:

1. **One agent, too many jobs.** Schema drafting, validation, test
   generation, and publication are distinct tasks. A single agent
   conflates them — a validation error in the YAML might be masked
   by the agent moving on to the next step before fully resolving it.
2. **No external validation.** The agent that writes the code also
   validates it — no second opinion. The Code Review Swarm proved
   that dedicated reviewers catch things authors miss.
3. **No knowledge grounding.** The single agent has schema examples
   in its prompt but doesn't query the knowledge base for design
   decisions, existing skills, or invariants.
4. **No edit capability.** The single agent only creates. It can't
   read an existing topology, understand what's missing, and propose
   targeted modifications.

The multi-agent approach addresses all four: the conversation leader
focuses on understanding intent, the schema drafter focuses on correct
YAML, the knowledge searcher checks what already exists, the validator
checks against invariants, and the publisher handles file I/O.

## Agent tree

```
root (authoring-supervisor)
├── conversation-leader
│   Talks to the user, understands intent, proposes plans
├── knowledge-searcher
│   Searches existing skills, MCP servers, design docs
├── schema-drafter
│   Generates YAML artifacts (skills, archetypes, topologies)
├── validator
│   Validates artifacts against schemas + design invariants
├── test-writer
│   Generates smoke tests for new skills
└── publisher
│   Writes files to the workspace (with provenance tagging)
```

**Root supervisor** — coordinates the authoring flow. For new
artifacts: conversation → knowledge search → draft → validate →
test → publish. For edits: conversation → read existing → knowledge
search → draft changes → validate → publish.

**Conversation leader** — the user-facing agent. Asks clarifying
questions, proposes a plan, confirms before proceeding. Never
generates YAML directly.

**Knowledge searcher** — before any artifact is drafted, searches
for existing skills, MCP servers, and design decisions that are
relevant. Uses the Knowledge MCP Server (`search_docs`,
`list_reference_skills`, `get_schema`). Prevents reinventing
existing capabilities.

**Schema drafter** — generates YAML artifacts given the conversation
leader's plan and the knowledge searcher's findings. Produces correct
YAML by referencing the schema via `get_schema` tool. For edits,
reads the existing file and produces a modified version.

**Validator** — independent validation agent. Checks every drafted
artifact against the JSON Schema (`validate_workspace`) and design
invariants (`search_docs` for relevant rules). Returns pass/fail
with specific issues. Drafts that fail go back to the schema drafter
for correction.

**Test writer** — generates a smoke test for each new skill. The
test verifies the skill resolves correctly and (for `llm_prompt`
skills) produces output matching the declared `outputs` schema.

**Publisher** — writes files to the workspace directory. Tags all
artifacts with `provenance.authored_by: authored_by_swarm`. Reports
what was written and where.

## Edit mode — modifying existing swarms

The highest-value new capability. Triggered when the user says
something like:

- "My code review swarm doesn't check for dependency vulnerabilities"
- "Add a notification step after the QA verdict"
- "Change the security reviewer's model to claude-opus-4-7"
- "The deploy-reviewer confidence threshold is too low, raise it to 0.9"

**Flow:**

1. **Conversation leader** understands the change request
2. **Knowledge searcher** reads the existing workspace state via
   `validate_workspace` to understand current topology/skills/archetypes
3. **Conversation leader** proposes a plan: "I'll add a
   `dependency-vulnerability-scan` skill, assign it to the
   security-reviewer archetype, and update the prompt"
4. **Schema drafter** reads the existing archetype YAML, generates
   the new skill YAML and the modified archetype YAML
5. **Validator** checks both against the schema and design invariants
6. **Publisher** writes the new skill and the modified archetype

The user never edits YAML directly — they describe what's wrong,
and the swarm figures out what files to change.

## Skill map

| Agent | Skills | MCP server |
|---|---|---|
| knowledge-searcher | query-swarmkit-docs, list-reference-skills, get-schema | swarmkit-knowledge |
| schema-drafter | get-schema, read-workspace-file | swarmkit-knowledge |
| validator | validate-workspace, query-swarmkit-docs | swarmkit-knowledge |
| test-writer | get-schema | swarmkit-knowledge |
| publisher | write-files | (filesystem, via tool) |
| conversation-leader | (none — delegates only) | — |

**New skills needed:**

- `list-reference-skills` — wraps `list_reference_skills` tool on
  the Knowledge MCP Server
- `get-schema` — wraps `get_schema` tool
- `validate-workspace` — wraps `validate_workspace` tool
- `read-workspace-file` — reads an existing YAML file from the
  workspace (for edit mode)

These are all `mcp_tool` skills backed by the Knowledge MCP Server
or simple filesystem operations. The authoring tools (`write_files`,
`validate_yaml`) from M3.5 remain available as runtime tools, not
skill-declared MCP tools — they're injected by the authoring session
handler.

## Provenance enforcement

All artifacts authored by this swarm get:

```yaml
provenance:
  authored_by: authored_by_swarm
  version: 1.0.0
```

Per design §8.8, swarm-authored artifacts require human review before
production use. The publisher agent sets this provenance automatically.
The runtime can optionally warn or block `authored_by_swarm` skills
from executing without explicit approval.

## Relation to M3.5 single-agent authoring

M3.5's single-agent authoring remains as the **quick path** — fast,
low-token, good for simple artifacts. The Skill Authoring Swarm is
the **quality path** — more tokens, more thorough, better for
complex artifacts and edits.

Both are accessible via CLI:
- `swarmkit author skill` → quick path (single agent, existing M3.5)
- `swarmkit author skill --thorough` → quality path (this swarm)
- `swarmkit edit <workspace>` → edit mode (this swarm, always)

## Dogfooding value

This topology exercises:
- Multi-agent coordination (6 agents, sequential delegation)
- Knowledge MCP Server integration (search, schema lookup, validation)
- Workspace file I/O (read existing + write new)
- Provenance tracking
- The full authoring → validation → publication pipeline

If SwarmKit can't build itself effectively, it can't build anything
else effectively. Gaps found here directly improve the framework.

## Implementation plan

### PR 1: Design note (this document)

Review before implementation.

### PR 2: New knowledge-backed skills + archetypes

Skills: list-reference-skills, get-schema, validate-workspace,
read-workspace-file. Archetypes: authoring-supervisor,
conversation-leader, knowledge-searcher, schema-drafter, validator,
test-writer, publisher.

### PR 3: Authoring swarm topology + `swarmkit edit` CLI

The topology YAML + the `swarmkit edit` CLI command that launches
edit mode against an existing workspace.

### PR 4: Integration + live tests

End-to-end: create a new skill via the authoring swarm, then edit
an existing archetype to reference it. Verify via `swarmkit validate`.
