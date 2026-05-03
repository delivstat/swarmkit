# CLI commands

## Core

| Command | Description |
|---|---|
| `swarmkit validate <workspace>` | Validate and resolve a workspace |
| `swarmkit validate <workspace> --tree` | Print resolved agent tree with skills, archetypes, MCP servers |
| `swarmkit run <workspace> <topology>` | Execute a topology one-shot |
| `swarmkit run ... --input "..."` | Provide input inline |
| `swarmkit run ... --verbose` | Run with per-agent execution summary (tools called, timing, denials) |
| `swarmkit run ... --dry-run` | Show resolved agents + skills without executing (no LLM/MCP calls) |
| `swarmkit serve <workspace>` | Start the HTTP server |
| `swarmkit chat <workspace> <topology>` | Multi-turn conversation (context persists across turns) |
| `swarmkit chat ... --resume <id>` | Resume a previous conversation |
| `swarmkit conversations <workspace>` | List saved conversations with last message preview |
| `swarmkit conversations ... --pick` | Pick a conversation to resume interactively |

### Chat features

The chat mode uses `prompt_toolkit` for a full terminal experience:

- **Arrow keys**: up/down for history, left/right for cursor movement
- **History search**: Ctrl+R to search previous inputs
- **Persistent history**: saved across sessions in `~/.swarmkit/chat_history`
- **Auto-complete**: topology commands and built-in commands

### Chat commands

These commands work inside `swarmkit chat`:

| Command | Description |
|---|---|
| `/model` | Show current model and provider |
| `/model <provider/model>` | Switch all agents to a different model (e.g. `/model deepseek/deepseek-chat`) |
| `/model reset` | Reset to topology YAML defaults |
| `exit` / `quit` / `bye` | End the conversation |

## Authoring

All authoring commands use `prompt_toolkit` with history and arrow key support.

| Command | Description |
|---|---|
| `swarmkit init [path]` | Create a workspace through conversation |
| `swarmkit edit <workspace>` | Edit a workspace through conversation |
| `swarmkit author topology <workspace>` | Author a topology |
| `swarmkit author skill <workspace>` | Author a skill |
| `swarmkit author archetype <workspace>` | Author an archetype |
| `swarmkit author mcp-server <workspace>` | Author an MCP server (scaffolds Python + skill YAML + workspace entry) |

Add `--thorough` to use the multi-agent authoring swarm instead of the single agent.

### Authoring provider

By default, authoring uses Ollama (local). Override with environment variables:

```bash
SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=deepseek/deepseek-chat \
  swarmkit author skill .
```

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
| `swarmkit knowledge-pack [-o file]` | Bundle corpus for LLM paste (~350KB markdown) |
| `swarmkit knowledge-server` | Launch Knowledge MCP Server (stdio, for Claude Code / Cursor) |

## Runtime behaviour

### Multi-turn tool loop

When an agent makes tool calls, the runtime executes them and feeds results back to the model for synthesis. The model can make additional tool calls — up to `SWARMKIT_MAX_TOOL_TURNS` rounds (default: 8). If the model responds with planning language ("let me examine...") instead of tool calls, the runtime nudges it to act.

### Conversation context

Worker agents receive the full conversation history from prior turns, so they can see previous findings and avoid redundant tool calls.

### Path sanitisation

When models send absolute file paths (common with grep results), the runtime converts them to relative paths within the MCP server's working directory.

### Verbose mode

Set `SWARMKIT_VERBOSE=1` or use `--verbose` to see per-agent detail:

```
--- [sterling-developer] calling deepseek/deepseek-chat ---
  tools: ['grep-project-code', 'read-file-lines', 'verify-code-citations', ...]
  input: Describe the Java class...
  tool_calls: ['grep-project-code']
  executing: grep-project-code
  [mcp args: {'pattern': 'SourcingRule'}]
  [tool loop turn 1: 1 tool results]
  executing: read-file-lines
  [mcp args: {'path': './java-code/src/.../Agent.java', 'start_line': 2080, 'end_line': 2216}]
  [tool loop turn 2: 1 tool results]
  [synthesis call with 2 tool results]
```

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

## Environment variables

| Variable | Purpose |
|---|---|
| `SWARMKIT_PROVIDER` | Override model provider for all agents |
| `SWARMKIT_MODEL` | Override model name for all agents |
| `SWARMKIT_VERBOSE` | Enable verbose output (set to `1`) |
| `SWARMKIT_MAX_TOOL_TURNS` | Max tool loop iterations per agent turn (default: 8) |
| `SWARMKIT_AGENT_RETRIES` | Max retries when model returns text instead of tools (default: 2) |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GOOGLE_API_KEY` | Google AI API key |
| `GROQ_API_KEY` | Groq API key |
| `TOGETHER_API_KEY` | Together API key |
