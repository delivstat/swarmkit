# Level 14: Packaging & Distribution

Share your workspace as an installable package — let others use your expertise from their AI assistant.

## What you'll learn

- `swarmkit publish` — bundle workspace for distribution
- `swarmkit install` — install packages from path, tarball, or URL
- `swarmkit packages` — list installed packages
- `swarmkit mcp-serve` — expose workspaces as MCP tools for AI assistants
- Package format and metadata

## Publish your workspace

### 1. Add package metadata

```yaml
# package.yaml (workspace root)
name: "@yourname/content-reviewer"
version: 1.0.0
description: >
  Multi-agent content review workspace. Three specialists
  (research, writing, security) coordinate to produce
  thorough content reviews.
author: Your Name <you@example.com>
license: MIT
requires:
  runtime: ">=1.3.0"
  providers:
    - openrouter
  env:
    - OPENROUTER_API_KEY
topologies:
  - content-team
  - structured-review
knowledge:
  searchable: true
```

### 2. Bundle it

```bash
swarmkit publish . --output ./dist
```

Creates `dist/yourname-content-reviewer-1.0.0.tar.gz` containing:
- Workspace YAML files
- Topologies, archetypes, skills
- Custom MCP server scripts
- Package metadata

Excludes: `.env`, `.swarmkit/`, `__pycache__/`, `.git/`, `*.sqlite`.

### 3. Install a package

```bash
# From a local directory
swarmkit install ./path-to-workspace/

# From a tarball
swarmkit install ./dist/yourname-content-reviewer-1.0.0.tar.gz

# From a URL (GitHub release)
swarmkit install https://github.com/yourname/content-reviewer/releases/download/v1.0.0/content-reviewer-1.0.0.tar.gz

# Upgrade existing installation
swarmkit install ./updated-workspace/ --upgrade
```

Packages install to `~/.swarmkit/packages/`.

### 4. List installed packages

```bash
swarmkit packages
```

```
Installed packages:
┌──────────────────────────────┬────────────┬─────────────────────┬─────────────────────────────────┐
│ Package                      │ Topologies │ Installed           │ Path                            │
├──────────────────────────────┼────────────┼─────────────────────┼─────────────────────────────────┤
│ @yourname/content-reviewer   │ 2          │ 2026-06-08          │ ~/.swarmkit/packages/yourname.. │
└──────────────────────────────┴────────────┴─────────────────────┴─────────────────────────────────┘
```

## Expose as MCP tools

### 5. SwarmKit as MCP server

Make all installed workspaces available as tools for AI assistants:

```bash
swarmkit mcp-serve ./my-swarm
```

This starts an MCP server on stdio. Each topology becomes a callable tool:

```
Tools available:
  - run_hello(input: str) — Run Hello World topology
  - run_content_team(input: str) — Run Content Team topology
  - search_knowledge(query: str) — Search workspace knowledge
  - list_workspaces() — Show available workspaces
```

### 6. Configure in Claude Desktop

```json
// ~/.claude/claude_desktop_config.json
{
  "mcpServers": {
    "swarmkit": {
      "command": "swarmkit",
      "args": ["mcp-serve", "/path/to/my-swarm"],
      "env": {
        "OPENROUTER_API_KEY": "sk-or-..."
      }
    }
  }
}
```

Now Claude Desktop can use your topologies as tools:

```
You: Review this PR for security issues

Claude: [calls swarmkit.run_structured_review(input="Review PR #42...")]
        [SwarmKit runs 3-agent code review]

Claude: "The security review found 2 critical issues..."
```

### 7. Multiple workspaces

Expose multiple workspaces at once:

```bash
swarmkit mcp-serve ./workspace1 ./workspace2 ./workspace3
```

Tools are namespaced to avoid conflicts:
- `run_workspace1_topology1`
- `run_workspace2_topology1`

## Your workspace so far

```
my-swarm/
├── package.yaml            # package metadata
├── workspace.yaml
├── dist/                   # published tarball
│   └── yourname-content-reviewer-1.0.0.tar.gz
└── ...                     # everything from previous levels
```

## Next

[Level 15: Production Example](15-production-example.md) — a complete workspace that uses every feature.
