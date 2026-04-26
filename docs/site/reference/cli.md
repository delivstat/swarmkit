# CLI commands

## Core

| Command | Description |
|---|---|
| `swarmkit validate <workspace>` | Validate and resolve a workspace |
| `swarmkit run <workspace> <topology>` | Execute a topology one-shot |
| `swarmkit serve <workspace>` | Start the HTTP server |
| `swarmkit init [path]` | Create a workspace through conversation |
| `swarmkit edit <workspace>` | Edit a workspace through conversation |

## Authoring

| Command | Description |
|---|---|
| `swarmkit author topology <workspace>` | Author a topology |
| `swarmkit author skill <workspace>` | Author a skill |
| `swarmkit author archetype <workspace>` | Author an archetype |
| `swarmkit author mcp-server <workspace>` | Author an MCP server |

Add `--thorough` to use the multi-agent authoring swarm instead of the single agent.

## Knowledge

| Command | Description |
|---|---|
| `swarmkit knowledge-pack [-o file]` | Bundle corpus for LLM |
| `swarmkit knowledge-server` | Launch Knowledge MCP Server |

## Review

| Command | Description |
|---|---|
| `swarmkit review list <workspace>` | List pending reviews |
| `swarmkit review show <id> <workspace>` | Show review details |
| `swarmkit review approve <id> <workspace>` | Approve a review |
| `swarmkit review reject <id> <workspace>` | Reject a review |
| `swarmkit gaps <workspace>` | List skill gaps |
