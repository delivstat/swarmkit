# CLI commands

## Core

| Command | Description |
|---|---|
| `swarmkit validate <workspace>` | Validate and resolve a workspace |
| `swarmkit run <workspace> <topology>` | Execute a topology one-shot |
| `swarmkit run ... --verbose` | Run with per-agent execution summary |
| `swarmkit run ... --dry-run` | Show resolved agents + skills without executing (no LLM/MCP calls) |
| `swarmkit serve <workspace>` | Start the HTTP server |
| `swarmkit chat <workspace> <topology>` | Multi-turn conversation (context persists across turns) |
| `swarmkit chat ... --resume <id>` | Resume a previous conversation |
| `swarmkit conversations <workspace>` | List saved conversations with last message preview |
| `swarmkit conversations ... --pick` | Pick a conversation to resume interactively |
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

## Observability

| Command | Description |
|---|---|
| `swarmkit status <workspace>` | Recent runs at a glance (topology, agents, duration, issues) |
| `swarmkit logs <workspace>` | Detailed events from past runs (`--last N`, `--topology filter`, `--format markdown`) |
| `swarmkit why <run-id> <workspace>` | LLM-powered explanation of what happened in a run |
| `swarmkit ask "question" -w <workspace>` | Conversational observer over workspace state + recent runs |

Run events are auto-saved to `.swarmkit/logs/` as JSONL after every `swarmkit run`.

## Review + gaps

| Command | Description |
|---|---|
| `swarmkit review list <workspace>` | List pending HITL review items |
| `swarmkit review show <id> <workspace>` | Show review details |
| `swarmkit review approve <id> <workspace>` | Approve a review |
| `swarmkit review reject <id> <workspace>` | Reject a review |
| `swarmkit gaps <workspace>` | List recorded skill gaps |

## Knowledge

| Command | Description |
|---|---|
| `swarmkit knowledge-pack [-o file]` | Bundle corpus for LLM |
| `swarmkit knowledge-server` | Launch Knowledge MCP Server (stdio) |

## HTTP server endpoints

Started via `swarmkit serve <workspace> [--port 8000] [--host 0.0.0.0]`.

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Workspace status |
| `/topologies` | GET | List available topologies |
| `/skills` | GET | List skills with categories |
| `/archetypes` | GET | List archetypes |
| `/run/{topology}` | POST | Execute a topology (`{"input": "...", "max_steps": 10}`) |
| `/validate` | GET | Resolved workspace state |
| `/conversations` | POST | Create a conversation (`{"topology": "..."}`) |
| `/conversations` | GET | List saved conversations |
| `/conversations/{id}/messages` | POST | Send a message (`{"message": "..."}`) |
