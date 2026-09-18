---
title: Skill registry — the `swarmkit skill` command over the catalogue and SKILL.md
description: How a workspace finds, adds, imports and checks skills — the swarmkit skill command group over the swarmkit-skills catalogue, a SKILL.md converter, and a liveness check. Refreshed September 2026 from the April proposal.
tags: [skills, registry, ecosystem, community]
status: accepted
---

# Skill registry — the `swarmkit skill` command

> **Refreshed 2026-09-18.** The April proposal below (kept under "Original landscape") is what
> this note now implements, with the two decisions [`skill-catalogue.md`](skill-catalogue.md)
> reversed in the meantime kept reversed: the library is the separately-versioned
> **`swarmkit-skills` catalogue**, not a registry inside the runtime wheel, and an entry's
> standing is its **verification date**, not its publisher. What was still missing on the day of
> the refresh: the command group itself. None of `swarmkit skill …` existed; `swarmkit install`
> moves whole workspaces, `swarmkit author skill` writes one from a conversation, and the
> catalogue was "copy a bundle by hand".

## What ships

```bash
swarmkit skill list                       # the workspace's skills: id, category, backing, who holds it
swarmkit skill list --available           # the catalogue: bundles, skills, verification dates
swarmkit skill search "git history"       # catalogue names + descriptions (and the workspace's own)
swarmkit skill show git-log               # one entry, from the workspace or the catalogue
swarmkit skill add git-log                # a catalogue skill: skills/git-log.yaml + its mcp_servers entry
swarmkit skill add git                    # a whole bundle: every skill + the server, one prompt
swarmkit skill add ./my-skill.yaml        # a local Skill file (or a SkillBundle), same path
swarmkit skill import ./SKILL.md          # an Agent Skills file → an llm_prompt skill
swarmkit skill check                      # do the tools the workspace's mcp_tool skills name still exist?
swarmkit skill remove git-log             # delete the file — refused while an archetype or agent holds it
```

`add` writes to **two places** — a new file under `skills/` (safe) and an `mcp_servers` entry in
`workspace.yaml` (hand-authored, not safe to edit silently) — so it shows both fragments and asks;
`--dry-run` prints them and writes nothing; `--yes` is for scripts. The `mcp_servers` edit goes
through the same comment-preserving, validate-or-roll-back service the portal's Connections page
uses (`server/_workspace_config.py`), so a hand edit and a `skill add` produce the same file.

### Sources, in order

1. **The workspace** — `skills/*.yaml` (for `list`, `show`, `search`, `remove`, `check`).
2. **The catalogue** — `delivstat/swarmkit-skills` on GitHub: `skills/<bundle>/bundle.yaml`
   (`kind: SkillBundle`: the `mcp_servers` block, the skill ids, `verification`) and
   `skills/<bundle>/skills/<id>.yaml`. Fetched over HTTPS, cached under
   `~/.swarmkit/cache/skills-catalogue.json` for a day (`--refresh` to refetch);
   `SWARMKIT_SKILLS_CATALOGUE=<dir-or-url>` points at a checkout or a mirror, which is also how the
   tests run against a fixture catalogue with no network.
3. **A path or URL** — a `Skill` or `SkillBundle` YAML file given to `add` directly.

`reference/skills/` in this repo stays what `skill-catalogue.md` said: worked examples the docs
teach from, versioned with the runtime — not a fourth source. Two of them (`governed-memory`,
`memory-reconcile`) are bundled by `memory-by-default.md` for a different reason.

### The converter (`import`)

An Agent Skills `SKILL.md` is YAML frontmatter (`name`, `description`, optional `metadata`) over a
markdown body of instructions. It becomes an `llm_prompt` skill:

```yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: <name, kebab-cased>
  name: <name, title-cased>
  description: <description>
category: capability
implementation:
  type: llm_prompt
  prompt: |
    <the markdown body, verbatim>
provenance:
  authored_by: imported_from_registry
  version: <metadata.version or 1.0.0>
  registry: <the file's origin — path or URL>
```

It is validated against `skill.schema.json` before it is written, and `add`'s two-place rule does
not apply (an `llm_prompt` skill needs no server). Anything the body references that a prompt
cannot do — scripts, bundled files — is left in the prompt for a person to see; the converter does
not pretend a SKILL.md with a `scripts/` directory is a prompt.

### `check`

For every `mcp_tool` skill in the workspace: start its server through the runtime's own MCP
client (the same start, sandbox and credentials a run would use), list the tools, and report
whether the tool the skill names is there — `ok`, `missing <tool>` (the server renamed it), or
`server failed: <reason>`. The catalogue's nightly job asks the same question of every entry; this
asks it of *yours*. It changes nothing; the exit code is non-zero when anything is not `ok`.

### Service, then CLI, then HTTP

Business logic lives in `swarmkit_runtime.skills._registry` (catalogue index and search, the add
planner and applier, the converter, the checker); `cli/_cmd_skill.py` and the serve routes
`GET /api/skill-catalogue`, `GET /api/skill-catalogue/{id}`, `POST /api/skills/add`,
`POST /api/skills/import`, `GET /api/skills/check` are thin over it — the CLI-first, thin-interface
rule. The portal's Skills page gets a **Library** tab over the same routes in a follow-up.

## Non-goals

- Bundling the catalogue into the runtime (`skill-catalogue.md`'s reversal stands).
- Importing MCP servers from a URL as a bundle (`import-mcp`). Curating a server — the permission
  tier, an `effects` map per tool, the argument shapes — is the catalogue's job and is done by a
  person with the server in front of them (`feedback: probe, don't transcribe`). `add` of a
  catalogue bundle is the supported way to get a server into a workspace.
- Auto-updating installed skills; `check` reports, it does not rewrite.
- A rating or marketplace system.

## Test plan

- catalogue: index built from a fixture directory (`SWARMKIT_SKILLS_CATALOGUE`); search ranks
  name and description hits; cache respected and `--refresh` bypasses it.
- add: the planner yields the skill file and the `mcp_servers` fragment; `--dry-run` writes
  nothing; apply writes the file and upserts the server through the config service (comments
  intact); re-running is idempotent; a bundle adds every skill and one server; an unknown id names
  the closest matches; a `requires_runtime` above the running runtime is refused naming both.
- import: frontmatter + body → a valid `llm_prompt` skill; a missing `name` is an error; the body
  is verbatim; `provenance.registry` carries the origin.
- check: against a stub MCP server — `ok` when the tool matches, `missing` when renamed,
  `server failed` when it will not start; non-zero exit when anything is not `ok`.
- remove: refused while an archetype or agent grants the skill; deletes otherwise.
- CLI: every subcommand invoked through the Typer app on a fixture workspace; HTTP routes through
  the TestClient.

## Demo plan

Level 13 gains a section run for real: `skill search git`, `skill add git` against the live
catalogue (the two fragments, the prompt, the resulting files), `skill check`, and `skill import`
of an Anthropic `SKILL.md`. Transcripts in the tutorial, the portal Skills page showing the added
skill.

---

## Original landscape and proposal (April 2026)

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

### CLI commands (as proposed; the shipped surface is above)

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
