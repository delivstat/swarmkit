---
title: Skill registry — community skill import + discovery
description: Architecture for importing skills from the Agent Skills (SKILL.md) and MCP ecosystems. CLI for install/search/list. Authoring AI integration.
tags: [skills, registry, ecosystem, community]
status: proposed
---

# Skill registry — community skill import + discovery

## Goal

SwarmKit users should find and install pre-built skills from the
community instead of writing everything from scratch. The ecosystem
has converged on two standards — both should be importable.

**Don't reinvent skills — import the ecosystem.**

## Landscape (as of April 2026)

Two dominant formats:

### Agent Skills (SKILL.md)

- **Spec:** agentskills.io (Apache-2.0)
- **Format:** YAML frontmatter + markdown body
- **Supported by:** 27+ agents (Claude Code, Gemini CLI, Codex,
  Cursor, Copilot, Windsurf)
- **Available skills:** 1,100+ cataloged (VoltAgent/awesome-agent-skills)
- **Key repos:**
  - github.com/anthropics/skills (123K stars) — official Anthropic
    skills for document creation, development, testing
  - github.com/vercel-labs/agent-skills — React, Next.js, deployment
  - github.com/google/skills — 13 Google Cloud product skills

**SKILL.md format:**

```yaml
---
name: lowercase-kebab-name
description: What it does
license: Apache-2.0
metadata:
  author: org-name
  version: "1.0"
---

# Instructions in Markdown

Progressive disclosure: ~100 tokens metadata at startup,
<5000 tokens body on activation, reference files on demand.
```

**Conversion to SwarmKit:** near-trivial — YAML frontmatter with
kebab-case IDs maps to SwarmKit's skill schema. The markdown body
becomes the skill's instruction content.

### MCP servers

- **Protocol:** JSON-RPC over stdio/SSE
- **Available:** 7,260+ servers cataloged (TensorBlock)
- **Key repos:**
  - github.com/modelcontextprotocol/servers (84K stars) — official
    reference servers (filesystem, git, fetch, memory)
  - Covers: databases, cloud platforms, APIs, search, communication
- **SwarmKit support:** already designed (§18). MCP tools are the
  implementation backend for capability skills.

### Other pools

| Source | Count | Format | SwarmKit path |
|---|---|---|---|
| LangChain tools | 600+ | Python classes | Runtime bridge (we compile to LangGraph) |
| Composio | 1,000+ | OpenAPI specs | Via Composio's MCP server |
| CrewAI tools | 60+ | Python classes | Wrap as skill implementation |
| OpenAI GPT Actions | 34 | OpenAPI specs | Parse spec → skill YAML |

## SwarmKit skill registry architecture

### Three-layer model

```
Community sources (remote)
  ├── Agent Skills repos (SKILL.md)
  ├── MCP server catalogs
  └── SwarmKit-native repos

         ↓ swarmkit skill import / install

Local registry (bundled with swarmkit-runtime)
  └── reference/skills/        ← 20+ pre-imported skills

         ↓ swarmkit skill install <name>

Workspace skills/
  └── skills/<name>.yaml       ← workspace-local, validated
```

### CLI commands

```bash
# Install from the local registry into the workspace
swarmkit skill install code-quality-review

# Import from a remote Agent Skills repo
swarmkit skill import github.com/anthropics/skills/create-docx

# Import an MCP server as a skill source
swarmkit skill import-mcp github.com/modelcontextprotocol/servers/filesystem

# Search available skills (local registry + remote catalogs)
swarmkit skill search "security"

# List installed skills in the workspace
swarmkit skill list

# List all available skills in the registry
swarmkit skill list --available
```

### SKILL.md → SwarmKit YAML converter

```
Input: SKILL.md
  ---
  name: code-quality-review
  description: Reviews code for quality issues
  metadata:
    author: anthropic
    version: "1.0"
  ---
  # Instructions...

Output: SwarmKit skill YAML
  apiVersion: swarmkit/v1
  kind: Skill
  metadata:
    id: code-quality-review
    name: Code Quality Review
    description: Reviews code for quality issues
  category: capability
  implementation:
    type: instruction
    content: |
      # Instructions...
  provenance:
    authored_by: community
    source: github.com/anthropics/skills/code-quality-review
    version: 1.0.0
```

The converter:
1. Parses YAML frontmatter → `metadata` block
2. Maps `name` → `id` (already kebab-case)
3. Infers `category` from content (or defaults to `capability`)
4. Preserves markdown body as `implementation.content`
5. Adds `provenance.source` tracking the origin repo
6. Validates against SwarmKit's skill schema

### Authoring AI integration

When the authoring AI is creating a workspace:

1. **Search first.** Before generating a new skill, search the
   registry: "I need a code quality review skill" → finds existing one
2. **Install existing.** If a match exists, propose installing it
   instead of generating
3. **Generate only gaps.** If no match, generate a new skill (current
   behavior)
4. **Cite sources.** When using a community skill, tell the user where
   it came from

The authoring AI's system prompt includes a catalog summary (skill
names + descriptions from the registry) so it knows what's available
without searching every time.

## Seed skills (initial registry, 20+)

Drawn from existing community repos:

**Capability skills:**
- `file-read` — read file contents (from MCP filesystem server)
- `file-write` — write file contents
- `web-fetch` — fetch URL content (from MCP fetch server)
- `web-search` — search the web
- `git-diff` — read git diff (from MCP git server)
- `git-log` — read git history
- `github-pr-read` — read pull request details
- `github-issue-read` — read issue details
- `database-query` — execute SQL query
- `code-execute` — run code in a sandbox

**Decision skills:**
- `code-quality-review` — code quality assessment (pass/fail + reasoning)
- `security-vulnerability-scan` — security check (severity + description)
- `content-moderation` — content safety check
- `schema-validation` — validate data against a schema

**Coordination skills:**
- `coordinate-workers` — leader-mediated worker collaboration

**Persistence skills:**
- `audit-log-write` — append to audit log
- `knowledge-base-update` — update shared knowledge
- `review-queue-submit` — submit item for human review

**Domain-specific (from Google/Anthropic repos):**
- `create-docx` — generate Word documents
- `create-pdf` — generate PDF documents
- `bigquery-query` — BigQuery SQL execution

## Implementation plan

1. **Task #44:** Design note (this document) ✓
2. **Task #45:** SKILL.md → SwarmKit YAML converter
3. **Task #46:** Seed 20+ skills from community repos
4. **Task #47:** `swarmkit skill install/search/list` CLI
5. **Follow-up:** Wire authoring AI to search registry before generating

## Non-goals (for now)

- **Publishing skills back to community repos.** Import only for v1.0;
  publishing is M7+ (Skill Authoring Swarm).
- **Skill marketplace / rating system.** Community skills are trusted
  by source (Anthropic, Google, MCP official). No rating system needed
  for v1.0.
- **Automatic skill updates.** Version pinning via `provenance.version`;
  updates are manual (`swarmkit skill update <name>`).
