# CLI commands

## Core

| Command | Description |
|---|---|
| `swael validate <workspace>` | Validate and resolve a workspace |
| `swael run <workspace> <topology>` | Execute a topology one-shot |
| `swael run ... --verbose` | Run with per-agent execution summary |
| `swael run ... --dry-run` | Show resolved agents + skills without executing (no LLM/MCP calls) |
| `swael serve <workspace>` | Start the HTTP server |
| `swael init [path]` | Create a workspace through conversation |
| `swael edit <workspace>` | Edit a workspace through conversation |

## Authoring

| Command | Description |
|---|---|
| `swael author topology <workspace>` | Author a topology |
| `swael author skill <workspace>` | Author a skill |
| `swael author archetype <workspace>` | Author an archetype |
| `swael author mcp-server <workspace>` | Author an MCP server |

Add `--thorough` to use the multi-agent authoring swarm instead of the single agent.

## Observability

| Command | Description |
|---|---|
| `swael status <workspace>` | Recent runs at a glance (topology, agents, duration, issues) |
| `swael logs <workspace>` | Detailed events from past runs (`--last N`, `--topology filter`, `--format markdown`) |
| `swael why <run-id> <workspace>` | LLM-powered explanation of what happened in a run |
| `swael ask "question" -w <workspace>` | Conversational observer over workspace state + recent runs |

Run events are auto-saved to `.swael/logs/` as JSONL after every `swael run`.

## Review + gaps

| Command | Description |
|---|---|
| `swael review list <workspace>` | List pending HITL review items |
| `swael review show <id> <workspace>` | Show review details |
| `swael review approve <id> <workspace>` | Approve a review |
| `swael review reject <id> <workspace>` | Reject a review |
| `swael gaps <workspace>` | List recorded skill gaps |

## Knowledge

| Command | Description |
|---|---|
| `swael knowledge-pack [-o file]` | Bundle corpus for LLM |
| `swael knowledge-server` | Launch Knowledge MCP Server (stdio) |

## HTTP server endpoints

Started via `swael serve <workspace> [--port 8000] [--host 0.0.0.0]`.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Workspace status |
| `/topologies` | GET | List available topologies |
| `/skills` | GET | List skills with categories |
| `/archetypes` | GET | List archetypes |
| `/run/{topology}` | POST | Execute a topology (`{"input": "...", "max_steps": 10}`) |
| `/validate` | GET | Resolved workspace state |
