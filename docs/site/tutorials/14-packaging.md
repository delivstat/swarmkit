# Level 14: Packaging & Distribution

A workspace is a directory of open files. This level makes it a thing you can hand to someone —
and a set of tools their AI assistant can call.

## What you'll learn

- `package.yaml` and `swarmkit publish` — a tarball of the workspace, secrets excluded
- `swarmkit install` and `swarmkit packages` — from a path, a tarball or a URL
- `swarmkit mcp-serve` — every topology as a tool for Claude Desktop, Cursor, Claude Code
- The SwarmKit knowledge server for an assistant that is helping you *write* workspaces

The finished workspace is `examples/tutorials/14-packaging/` — Level 13 plus `package.yaml`.
Every command below was run; the outputs are what it printed.

## Publish

### 1. Describe the package

```yaml
# package.yaml — at the workspace root
name: "@my-org/handbook-swarm"
version: 1.0.0
description: >
  A librarian that answers from the company handbook with citations, a chat assistant with
  memory, and the content team — everything built in Levels 1–13.
author: Srijith Kartha <srijith.kartha@delivstat.com>
license: MIT
requires:
  runtime: ">=1.227.0"
  providers:
    - openrouter
  env:
    - OPENROUTER_API_KEY
topologies:
  - librarian
  - hello
  - content-team
```

`name` is the package's identity (`@scope/name` is conventional); `requires` tells an installer
what the workspace needs from its environment before a run can succeed.

### 2. Bundle

```bash
swarmkit publish . --output ./dist
```

```
Package created: …/14-packaging/dist/my-org-handbook-swarm-1.0.0.tar.gz
  45 files, 13 KB

Install with: swarmkit install …/dist/my-org-handbook-swarm-1.0.0.tar.gz
```

What went in — the artifacts, the servers, the knowledge, the triggers, the package file:

```
archetypes/…                 skills/…                 topologies/… (incl. hello/hello-v0.4.0.yaml)
funnels/design-gate.yaml     roles/leads.yaml         triggers/morning-brief.yaml · pr-opened.yaml
knowledge/docs/…             servers/*.py             package.yaml · workspace.yaml
```

What did not: `.env` and `.env.*`, `.swarmkit/` (the store, the audit log, the prompt buffer),
`*.sqlite`, `*.db`, `dist/`, `__pycache__/`, `.git/`, `node_modules/` — and whatever the
workspace's own `.gitignore` names, which is how `knowledge/chromadb/` (the vector index from
Level 10) stays out. Credentials never travel — the workspace refers to them by name
(`{credential.…}`, `env:` refs) and the installer's environment supplies them.

## Install

```bash
swarmkit install ./dist/my-org-handbook-swarm-1.0.0.tar.gz    # a tarball
swarmkit install ./some-workspace/                             # a directory
swarmkit install https://example.com/releases/handbook-swarm-1.0.0.tar.gz   # a URL
swarmkit install ./dist/my-org-handbook-swarm-1.0.0.tar.gz --upgrade         # replace an installed version
```

```
Installed @my-org/handbook-swarm → /home/you/.swarmkit/packages/my-org_handbook-swarm
```

```bash
swarmkit packages
```

```
                               Installed packages
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Package             ┃ Topologies ┃ Installed           ┃ Path                ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ @my-org/handbook-s… │         10 │ 2026-09-17T22:17:20 │ /home/srijith/.swa… │
└─────────────────────┴────────────┴─────────────────────┴─────────────────────┘
```

An installed package is a workspace like any other: `swarmkit run ~/.swarmkit/packages/my-org_handbook-swarm librarian --input …`
and `swarmkit serve ~/.swarmkit/packages/my-org_handbook-swarm` both work on it.

## Topologies as tools for an AI assistant

`swarmkit mcp-serve` is an MCP server on stdio — the transport Claude Desktop, Cursor and Claude
Code speak — over one or more workspaces. Each topology is a tool.

```bash
swarmkit mcp-serve ~/.swarmkit/packages/my-org_handbook-swarm
```

Listing its tools with the MCP SDK's client:

```
  run_analysis — [my-swarm] One analyst whose structured verdict is gated by the design funnel.
  run_content-team — [my-swarm] A coordinator delegates research and writing tasks to specialist agents…
  run_hello — [my-swarm] A single agent using an archetype.
  run_librarian — [my-swarm] One agent that searches the handbook and cites its sources.
  run_translator — [my-swarm] Run translator topology
  …
  search_knowledge — [my-swarm] Search the workspace knowledge base.
  list_workspaces — List installed SwarmKit workspaces and their topologies.
```

The description is the topology's `metadata.description` — write it for the assistant that will
choose between tools. Calling one:

```
run_librarian({"input": "What is the hotel cap per night in a metro?"})
→ The hotel cap per night in a metro is **₹9,000**.
  *(Source: knowledge/docs/expense-policy.md, Travel section)*
  ---
  Tokens: 883 (moonshotai/kimi-k2.5: {'input': 772, 'output': 111, 'total': 883, 'cost': 0.00063})
```

The run happened in the installed package — its gates, its memory, its audit log — and the
assistant got the answer with a cost line. A versioned topology's `hello@0.4.0` keys are not
exposed as tools (their names are outside the MCP alphabet); `run_hello` runs the version the
workspace routes to.

### Claude Desktop / Cursor / Claude Code

```json
{
  "mcpServers": {
    "handbook": {
      "command": "swarmkit",
      "args": ["mcp-serve", "/home/you/.swarmkit/packages/my-org_handbook-swarm"],
      "env": { "OPENROUTER_API_KEY": "sk-or-…" }
    }
  }
}
```

That is the standard MCP server entry (`~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS, `.cursor/mcp.json`, `claude mcp add handbook -- swarmkit mcp-serve …` for Claude Code).
The assistant then sees `run_librarian` and calls it when a question is about the handbook.

Several workspaces at once namespace their tools by workspace id:

```bash
swarmkit mcp-serve ./handbook ./ops-runbooks     # run_<workspace>_<topology>, search_<workspace>_knowledge
```

Level 11's `/mcp/` endpoint on `swarmkit serve` is the same idea over HTTP, behind the server's
auth — for assistants and agents that reach the workspace over the network rather than launching
it.

## The other direction: an assistant that writes workspaces

`swarmkit knowledge-server` (Level 10) is an MCP server over **SwarmKit's own** corpus — schemas,
design notes, reference skills, `validate_workspace`, `read_workspace_file`,
`write_workspace_file`. Point Claude Code or Cursor at it and the assistant helping you author a
workspace can look up the topology schema, check a file, and validate what it wrote:

```bash
claude mcp add swarmkit-knowledge -- swarmkit knowledge-server
```

It runs from a SwarmKit source checkout today (it finds the corpus by walking up to the
repository), the same limit noted for the authoring swarm in Level 13.

## Your workspace so far

```
my-swarm/
├── package.yaml                 # identity, requirements, the topologies to advertise
├── dist/
│   └── my-org-handbook-swarm-1.0.0.tar.gz
└── …                            # everything from Levels 1–13
```

## Next

[Level 15: Production Example](15-production-example.md) — one workspace that uses all of it, deployed.
