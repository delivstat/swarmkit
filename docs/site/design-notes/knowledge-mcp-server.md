---
title: Knowledge MCP Server — live SwarmKit corpus for any MCP client
description: A stdio MCP server exposing SwarmKit documentation, schemas, examples, and workspace state as searchable tools. The live counterpart to swarmkit knowledge-pack.
tags: [knowledge, mcp, llm-friendly, m5]
status: proposed
---

# Knowledge MCP Server

## Goal

Any MCP client — Claude Code, Cursor, the authoring agent, `swarmkit
ask`, a custom IDE plugin — can query SwarmKit's own documentation
live. Instead of pasting a 350 KB knowledge pack into a system prompt,
the client calls targeted tools: search the design docs, look up a
schema, inspect a workspace's resolved state.

This is the live counterpart to `swarmkit knowledge-pack`. The pack is
a one-shot dump for paste-into-any-LLM. This server is a persistent,
queryable interface over the same corpus.

## Non-goals

- **Domain-specific knowledge.** This server exposes SwarmKit's own
  docs and schemas, not the user's codebase or business data. That's
  the Knowledge Curator topology (`design/details/knowledge-curator.md`).
- **Vector embeddings in v1.** The corpus is ~350 KB — keyword search
  is effective. Vector search (via Qdrant MCP) is an enhancement, not
  a prerequisite.
- **Write operations.** This is a read-only server. Docs are authored
  by humans and committed to the repo. The server reads from disk.

## Architecture

```
SwarmKit repo / installed package
    ├── design/details/*.md         (design notes)
    ├── docs/notes/*.md             (discipline notes)
    ├── packages/schema/schemas/    (JSON Schemas)
    ├── reference/skills/           (reference skills)
    ├── examples/                   (example workspaces)
    └── llms.txt                    (static index)
        ↓
Knowledge MCP Server (stdio)
    ├── search_docs(query)          → ranked text results
    ├── get_schema(artifact_type)   → JSON Schema
    ├── get_design_note(slug)       → full design note
    ├── list_design_notes()         → index with frontmatter
    ├── list_schemas()              → available schema names
    ├── get_error_reference(code)   → error description + fix
    ├── validate_workspace(path)    → resolved state or errors
    └── list_reference_skills()     → reference skill catalogue
        ↓
Any MCP client (Claude Code, Cursor, authoring agent, swarmkit ask)
```

## Tool surface

### `search_docs`

```
search_docs(query: string, max_results?: int = 5) → SearchResult[]
```

Keyword search across the entire corpus (design notes, discipline
notes, schemas, README, CLAUDE.md files). Returns ranked results with
file path, matched section heading, and a context snippet. Uses
simple term-frequency ranking — no embeddings required.

### `get_schema`

```
get_schema(artifact_type: "topology" | "skill" | "archetype" | "workspace" | "trigger") → object
```

Returns the canonical JSON Schema for the named artifact type. This
is what the authoring agent needs when generating YAML — the exact
shape, not a prose description of it.

### `get_design_note`

```
get_design_note(slug: string) → { frontmatter: object, content: string }
```

Returns a specific design note by slug (e.g. `mcp-client`,
`governance-provider-interface`). Frontmatter parsed separately so
clients can filter by tags or status.

### `list_design_notes`

```
list_design_notes(tag?: string) → DesignNoteEntry[]
```

Lists all design notes under `design/details/` with their frontmatter
(title, description, tags, status). Optional tag filter. This is the
table of contents the authoring agent or `swarmkit ask` uses to decide
which note to read in full.

### `list_schemas`

```
list_schemas() → string[]
```

Returns the list of available schema names: `["topology", "skill",
"archetype", "workspace", "trigger"]`.

### `get_error_reference`

```
get_error_reference(code: string) → { code: string, description: string, fix: string }
```

Looks up a validation error code (e.g. `agent.unknown-archetype`) and
returns the description + suggested fix. Error codes are grep-friendly
against the topology-loader design note. This tool lets an LLM explain
a validation failure to a user without having the full corpus loaded.

### `validate_workspace`

```
validate_workspace(path: string) → ValidationResult
```

Resolves a workspace directory and returns either the resolved tree
(topology names, agent IDs, skill bindings) or structured validation
errors. Wraps the same `resolve_workspace` the CLI uses.

### `list_reference_skills`

```
list_reference_skills() → ReferenceSkill[]
```

Lists the reference skills under `reference/skills/` with their
metadata: id, name, description, category, MCP server + tool. This is
what the authoring agent checks before generating a new skill — "does
a reference skill already cover this?"

## Implementation

### Single-file FastMCP server

```
packages/runtime/src/swarmkit_runtime/knowledge/_server.py
```

Uses `mcp.server.fastmcp.FastMCP`, same pattern as
`examples/hello-swarm/workspace/hello_world_server.py`. The server
reads from disk at tool-call time (no startup index, no background
process). The corpus is small enough that file I/O per call is
acceptable — under 100 ms for any tool.

### CLI launcher

```bash
swarmkit knowledge-server                  # stdio mode (for MCP clients)
swarmkit knowledge-server --repo /path     # override repo root
```

A Typer subcommand in `cli/__init__.py` that launches the server.
Defaults to the current directory's repo root (found by walking up
to the nearest `.git`).

### Corpus discovery

Reuses the same file-discovery logic as `swarmkit knowledge-pack`:

| Category | Source |
|---|---|
| Design notes | `design/details/*.md` (excluding README, _template) |
| Discipline notes | `docs/notes/*.md` (excluding README) |
| Canonical schemas | `packages/schema/schemas/*.json` |
| Reference skills | `reference/skills/*.yaml` |
| Project overview | `README.md`, `CLAUDE.md`, `llms.txt` |
| Package invariants | `packages/*/CLAUDE.md` |

The server finds the repo root once at startup and reads files
relative to it. No file watching — clients get the current state at
call time.

### Search implementation (v1)

Simple keyword search:
1. At tool-call time, read each file in the corpus.
2. Split into sections (by `## ` headings for markdown, by top-level
   keys for JSON/YAML).
3. Score sections by term frequency against the query.
4. Return top-N sections with path + heading + snippet.

This is adequate for a ~350 KB corpus. If retrieval quality becomes a
problem, add vector search as v2 via the Qdrant MCP server the
Knowledge Curator already uses.

## Workspace integration

The server can be declared in any workspace's `mcp_servers` block so
agents can query it during execution:

```yaml
mcp_servers:
  - id: swarmkit-knowledge
    transport: stdio
    command: ["uv", "run", "swarmkit", "knowledge-server"]
```

Skills that use it:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: query-swarmkit-docs
  name: Query SwarmKit Documentation
  description: Searches SwarmKit design docs, schemas, and examples.
category: capability
implementation:
  type: mcp_tool
  server: swarmkit-knowledge
  tool: search_docs
provenance:
  authored_by: human
  version: 1.0.0
```

## Consumers

| Consumer | How it uses the server |
|---|---|
| **Authoring agent** (`swarmkit init/author`) | Queries `get_schema` for exact YAML shape; `list_reference_skills` before generating new skills; `search_docs` when the user asks about a design concept. |
| **Claude Code / Cursor** | User adds the server to their MCP config. "How does SwarmKit governance work?" → `search_docs("governance")` → returns §8 sections. |
| **`swarmkit ask`** (task #36) | Uses `search_docs` + `get_design_note` as the retrieval layer instead of bundling the full pack inline. Cheaper, more targeted. |
| **CI / scripts** | `validate_workspace` as a programmatic check without parsing CLI output. |

## Relation to existing tools

| Tool | Purpose | Live? |
|---|---|---|
| `llms.txt` | Static index at repo root | No — snapshot |
| `swarmkit knowledge-pack` | One-shot dump of full corpus | No — snapshot |
| **Knowledge MCP Server** | Live query over corpus | **Yes** |
| Knowledge Curator topology | Domain-specific RAG (codebase, business data) | Yes — scheduled |

The progression: `llms.txt` tells an LLM where to look. `knowledge-pack`
gives it everything at once. The Knowledge MCP Server lets it ask
targeted questions. The Knowledge Curator adds domain-specific knowledge
that isn't in the repo.

## Test plan

- **Unit tests:** each tool function tested against the real repo files
  (no mocks — the corpus is committed). `search_docs("governance")`
  returns results mentioning §8. `get_schema("skill")` returns a valid
  JSON Schema. `list_design_notes()` returns entries with frontmatter.
- **Integration test:** launch the server via `stdio_client`, call
  each tool, verify structured responses.
- **Live pipeline test:** add `swarmkit-knowledge` to the hello-swarm
  workspace's `mcp_servers`, run a topology that queries it, verify
  the agent gets real design content back.

## Implementation plan

### PR 1 (this design note)

Design review before implementation.

### PR 2: Core server + CLI launcher

- `packages/runtime/src/swarmkit_runtime/knowledge/_server.py`
- `swarmkit knowledge-server` CLI subcommand
- Tools: `search_docs`, `get_schema`, `list_schemas`,
  `get_design_note`, `list_design_notes`
- Unit tests + stdio integration test

### PR 3: Workspace + error tools

- Tools: `validate_workspace`, `get_error_reference`,
  `list_reference_skills`
- Reference skill: `query-swarmkit-docs`
- Live pipeline test

### PR 4 (optional): Authoring agent integration

- Update authoring prompts to declare `swarmkit-knowledge` in the
  workspace's MCP servers during `swarmkit init`
- Authoring agent uses `get_schema` instead of inline schema examples
  in the system prompt (smaller prompt, always current)
