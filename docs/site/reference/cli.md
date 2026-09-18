# CLI commands

## Every command

<!-- BEGIN GENERATED: commands -->
68 commands, from the CLI itself (`swarmkit <command> --help` for the options).

| Command | What it does |
|---|---|
| `swarmkit adapters approve` | Approve a workspace adapter's current launch block (a human action). Inspect it with |
| `swarmkit adapters build` | Warm the build-in-sandbox image cache for an adapter (no local harness install needed). |
| `swarmkit adapters list` | List every available adapter kind and, for workspace adapters, its launch-approval status. |
| `swarmkit adapters show` | Show an adapter's launch command + fingerprint — what a reviewer inspects before approval. |
| `swarmkit artifacts get` | Print one artifact's content. |
| `swarmkit artifacts list` | List artifact refs recorded under one correlation id. |
| `swarmkit ask` | Ask a question about the workspace or recent runs. |
| `swarmkit auth token` | Mint a serve API token: generate a strong secret and print the config to wire it. |
| `swarmkit author archetype` | Author a new archetype through conversation. |
| `swarmkit author mcp-server` | Author a new MCP server through conversation. |
| `swarmkit author skill` | Author a new skill through conversation. |
| `swarmkit author topology` | Author a new topology through conversation. |
| `swarmkit chat` | Interactive multi-turn conversation with a topology. |
| `swarmkit checkpoints` | List checkpointed runs that can be resumed. |
| `swarmkit cited-change` | Check a change-rationale cites the code its diff changed (exit 1 if uncited). |
| `swarmkit comprehension` | Comprehension-debt signals from the audit log (read-only, never a gate). |
| `swarmkit connect` | Run the Mode B poll connector for a NAT'd / edge instance (design §13). |
| `swarmkit conversations` | List saved conversations. Use --pick to resume one interactively. |
| `swarmkit debug` | Retrieve LLM prompts and responses from the local ring buffer. |
| `swarmkit docs-reader` | Launch the Document Reader MCP Server (stdio). |
| `swarmkit edit` | Edit an existing workspace through conversation (M7 Skill Authoring Swarm). |
| `swarmkit eval` | Run an eval-set and score the topology (design §M15). |
| `swarmkit fleet enroll-token` | Mint a one-time enrollment token for a fleet to register with this instance. |
| `swarmkit fleet memberships` | List the fleets registered with this instance (no secrets). Shows each membership's scope, |
| `swarmkit gaps` | List recorded skill gaps. |
| `swarmkit init` | Create a new SwarmKit workspace through conversation. |
| `swarmkit install` | Install a SwarmKit expertise package. |
| `swarmkit knowledge-pack` | Bundle SwarmKit docs + schemas + workspace state into a paste-ready prompt. |
| `swarmkit knowledge-server` | Launch the SwarmKit Knowledge MCP Server (stdio). |
| `swarmkit logs` | Show events from recent topology runs. |
| `swarmkit mcp-serve` | Expose workspace topologies as MCP tools on stdio. |
| `swarmkit memory add` | Write a fact into governed memory, through the same path an agent writes through. |
| `swarmkit memory get` | Show the current memory for a (subject, attribute) key, optionally with its full history. |
| `swarmkit memory quarantine` | List quarantined contradictions awaiting (or resolved by) a curator. |
| `swarmkit memory resolve` | Resolve a quarantined contradiction — the one hard human gate in the memory path (§8). |
| `swarmkit memory search` | Search governed memory (relevance-ranked; empty query lists all by confidence). |
| `swarmkit packages` | List installed SwarmKit expertise packages. |
| `swarmkit providers list` | List every declared provider, its family, its source, and whether it is ready. |
| `swarmkit providers show` | Show a provider resolved through its chain — what actually reaches the family. |
| `swarmkit publish` | Package a workspace for distribution. |
| `swarmkit review answer` | Answer a harness input request (§6.3) with text. Inspect it first with `review show <id>`. |
| `swarmkit review approve` | Approve a pending review item. |
| `swarmkit review gate` | Whether a gate is resolved, with its approval policy applied. |
| `swarmkit review list` | List pending review items. |
| `swarmkit review reject` | Reject a pending review item. |
| `swarmkit review resolve` | Resolve a multi-party approval role-task as *identity*. |
| `swarmkit review show` | Show full details of a review item. |
| `swarmkit run` | One-shot execution of a topology (design §14.1). |
| `swarmkit serve` | Start the SwarmKit HTTP server (design §14.1). |
| `swarmkit skill add` | Add a skill (and the MCP server it needs) to this workspace. |
| `swarmkit skill check` | Start each mcp_tool skill's server and ask whether its tool still exists. |
| `swarmkit skill import` | Import an Agent Skills SKILL.md as an llm_prompt skill. |
| `swarmkit skill list` | The workspace's skills — or, with --available, the catalogue's. |
| `swarmkit skill remove` | Delete skills/<id>.yaml — refused while an archetype or agent holds the skill. |
| `swarmkit skill search` | Search the catalogue (and this workspace's own skills). |
| `swarmkit skill show` | One skill (or bundle), from the workspace if it has it, else the catalogue. |
| `swarmkit slice-check` | Check a diff against a slice budget — keep slices reviewable (exit 1 if over budget). |
| `swarmkit status` | Show recent run status at a glance. |
| `swarmkit stop` | Ask a running run to stop at its next agent boundary. |
| `swarmkit storage migrate` | Copy this workspace's local SQLite rows into its configured Postgres store. |
| `swarmkit storage status` | Show which backend each store resolves to, and where that decision came from. |
| `swarmkit system` | Versions, storage resolution, workspace properties and environment — what this instance is. |
| `swarmkit trace` | Show the agent call graph and token usage for a run. |
| `swarmkit trust apply` | Apply a proposal: add the capability to the archetype's ``executor.config.allowed_tools`` and |
| `swarmkit trust clear` | Lift a denial block and reset a pair's tally so it can accrue toward a proposal again. |
| `swarmkit trust list` | List pending allowlist-changeset proposals (archetype ← capability + the approval count). |
| `swarmkit validate` | Validate a SwarmKit workspace and print a resolved tree or errors. |
| `swarmkit why` | Explain what happened in a run using an LLM. |
<!-- END GENERATED: commands -->

## The ones you will use first

| Command | Description |
|---|---|
| `swarmkit validate <workspace>` | Validate and resolve a workspace (`--tree` prints the resolved agent tree; `--require` reports declared config no code path reaches; `--require-verified` reports outputs nothing checks) |
| `swarmkit run <workspace> <topology>` | Execute a topology one-shot (`--input "..."`, `--verbose`, `--dry-run`, `--resume`, `--correlation-id`, `--label k=v`) |
| `swarmkit run ... --attach <path>` | Put a file in front of the entry agent; repeatable, workspace-relative. The media type is read from the file's content, not its name — hence one `--attach` rather than `--image`/`--pdf`. Images only today; a bad path or an uncarryable type fails before the run starts, and every attachment is audited by name, type, size and SHA-256, never by content |
| `swarmkit serve <workspace>` | Start the HTTP server (and the portal, with the `[ui]` extra) — [Serve mode](serve.md), [HTTP API](http-api.md) |
| `swarmkit chat <workspace> <topology>` | Multi-turn conversation (`--resume <id>` continues one) |
| `swarmkit conversations <workspace>` | List saved conversations (`--pick` to resume one interactively) |
| `swarmkit providers list [workspace]` | Every model provider, its family, and whether its key is set — [Model provider](model-provider.md) |
| `swarmkit adapters list [workspace]` | Every harness adapter and, for workspace adapters, its launch-approval status — [Executor adapter](executor-adapter.md) |

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
| `swarmkit status <workspace>` | Recent runs at a glance — reads from AuditProvider (SQLite), falls back to JSONL |
| `swarmkit logs <workspace>` | Detailed events from past runs. Filters: `--last N`, `--run-id`, `--agent`, `--topology`, `--format markdown` |
| `swarmkit why <run-id> <workspace>` | LLM-powered explanation — reads from AuditProvider, falls back to JSONL |
| `swarmkit ask "question" -w <workspace>` | Conversational observer with structured audit context. Use `--run <id>` to scope |
| `swarmkit debug <workspace>` | Query local prompt ring buffer (prompts never leave your machine) |
| `swarmkit debug ... --span-id <id>` | Retrieve prompt/response for a specific OTel span |
| `swarmkit debug ... --run-id <id>` | All prompts for a run |
| `swarmkit debug ... --agent <name> -n 5` | Last N prompts for an agent |
| `swarmkit stop <run-id> <workspace>` | Ask a run to stop at its next agent boundary. Cooperative, not a kill: a call in flight finishes first, the run keeps everything it has already done, and it resumes with `swarmkit run … --resume`. Works across processes — it writes a durable flag, so it can stop a run `swarmkit serve` started. Stopping a finished run is a no-op, not an error. |

### Data sources

Events are persisted to `.swarmkit/audit.sqlite` (SQLite, default) after every `swarmkit run`. All observability commands read from this store via `WorkspaceRuntime.audit_provider_for()` — the same service layer the web UI will use. JSONL logs (`.swarmkit/logs/`) are kept as a fallback.

Prompts are stored separately in `.swarmkit/prompts.sqlite` (local ring buffer). They never leave your environment — use `swarmkit debug` to access them.

### Audit redaction

Skills can declare audit policies in YAML:

```yaml
audit:
  log_inputs: summary     # full | summary | none
  log_outputs: full
  redact: ["$.password", "$.api_key"]
```

Redacted fields appear as `[REDACTED]` in all outputs. Summary mode truncates long values. Workspace-level `audit.level` (minimal/standard/detailed) clamps all skills.

## Review + gaps

| Command | Description |
|---|---|
| `swarmkit review list <workspace> [--kind permission\|input\|role_task] [--gate <id>]` | List pending HITL review items |
| `swarmkit review show <id> <workspace>` | Show review details |
| `swarmkit review approve <id> <workspace>` | Approve a review |
| `swarmkit review reject <id> <workspace>` | Reject a review |
| `swarmkit review resolve <id> --as <identity> [--approve\|--reject] <workspace>` | Resolve a multi-party approval role-task, recording the resolver (checked against the [role registry](role-registry.md)) |
| `swarmkit gaps <workspace>` | List recorded skill gaps |

## Knowledge

| Command | Description |
|---|---|
| `swarmkit knowledge-pack [--lean] [-o file]` | Bundle the corpus for an LLM: `--lean` (~190k tokens: overview, generated CLI/HTTP reference, schemas, design doc, guides) or full (~610k tokens: plus every design note, historical ones last under a banner) |
| `swarmkit knowledge-server` | Launch Knowledge MCP Server (stdio, for Claude Code / Cursor) |

## Runtime behaviour

### Multi-turn tool loop

When an agent makes tool calls, the runtime executes them and feeds results back to the model for synthesis. The model can make additional tool calls — up to `SWARMKIT_MAX_TOOL_TURNS` rounds (default: 50; `SWARMKIT_MAX_PER_TOOL` caps calls to any one search/write tool at 8 and `SWARMKIT_MAX_PER_READ_TOOL` any one read-only tool at 50). If the model responds with planning language ("let me examine...") instead of tool calls, the runtime nudges it to act.

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

## Storage + system info

| Command | Description |
|---|---|
| `swarmkit storage status [ws]` | Which backend each store resolves to, and which setting decided it |
| `swarmkit storage migrate [ws]` | Copy local SQLite rows into the configured Postgres (`--dry-run`, `--yes`) |
| `swarmkit system [ws]` | Versions, storage, workspace properties and environment (`--all` includes unset vars) |

Secrets are masked in all three: a path listed under `secrets:` in `workspace.env.yaml` prints as
`set`, and connection URLs print without the password. See [Storage](storage.md) for the full
SQLite → Postgres runbook.

## HTTP server endpoints

Started via `swarmkit serve <workspace> [--port 8000] [--host 0.0.0.0]`. Every endpoint is listed in the generated [HTTP API](http-api.md) reference; the prose — auth, triggers, attachments, streaming — is in [Serve mode](serve.md).

## Environment variables

<!-- BEGIN GENERATED: env -->
48 variables, from the runtime's own registry (`swarmkit system` and `GET /system` report the same list, secrets masked). A variable the code reads and the registry does not know fails a test.

**Storage**

| Variable | Purpose |
|---|---|
| `SWARMKIT_STORE_URL` | Connection URL for every store. Set alone it also SELECTS postgres — a URL names its own backend. Overrides storage.runtime.url in workspace.yaml. *(URL; userinfo masked)* |
| `SWARMKIT_STORE_BACKEND` | Force the backend (sqlite \| postgres) regardless of workspace.yaml. Optional: setting only the URL is enough. |
| `DATABASE_URL` | Fallback connection URL when SWARMKIT_STORE_URL is unset. *(URL; userinfo masked)* |
| `SWARMKIT_WORKSPACE` | Default workspace root for commands that omit it. |
| `SWARMKIT_SKILLS_CATALOGUE` | Where `swarmkit skill` reads the catalogue: a checkout directory or a mirror URL (default: the swarmkit-skills repo on GitHub). |
| `SWARMKIT_GATES_DIR` | Where file-backed approval gates are written. |

**Models**

| Variable | Purpose |
|---|---|
| `SWARMKIT_PROVIDER` | Default model provider when a topology names none. |
| `SWARMKIT_MODEL` | Default model when a topology names none. |
| `SWARMKIT_JUDGE_MODEL` | Model used by governance decision skills. |
| `SWARMKIT_AUTHOR_MODEL` | Model used by the authoring swarms. |
| `SWARMKIT_MODEL_TIMEOUT` | Per-call timeout in seconds. |
| `SWARMKIT_MODEL_RETRIES` | Retries per model call before the node fails. |
| `ANTHROPIC_API_KEY` | Anthropic credential. *(secret)* |
| `OPENAI_API_KEY` | OpenAI credential. *(secret)* |
| `OPENROUTER_API_KEY` | OpenRouter credential. *(secret)* |
| `GOOGLE_API_KEY` | Google GenAI credential. *(secret)* |

**Run limits**

| Variable | Purpose |
|---|---|
| `SWARMKIT_MAX_TOOL_TURNS` | Tool-calling turns before a node is cut off. |
| `SWARMKIT_MAX_TOOLS` | Tools exposed to one agent. |
| `SWARMKIT_MAX_RESULT_CHARS` | Truncation ceiling for a tool result. |
| `SWARMKIT_MAX_DELEGATIONS_PER_CHILD` | Delegation fan-out cap per child. |
| `SWARMKIT_MAX_PER_TOOL` | Calls to one search/write tool per turn (8). |
| `SWARMKIT_MAX_PER_READ_TOOL` | Calls to one read-only tool per turn (50). |
| `SWARMKIT_READ_TOOL_PREFIXES` | Comma-separated name prefixes that mark a tool read-only (read-, get-, list-, ...). |
| `SWARMKIT_READ_TOOLS` | Comma-separated tool names treated as read-only. |
| `SWARMKIT_ATTACHMENT_MAX_BYTES` | Ceiling per run attachment before the upload (20 MiB). |
| `SWARMKIT_AGENT_RETRIES` | Retries for a failing agent node. |
| `SWARMKIT_HISTORY_TURNS` | Conversation turns replayed into context. |
| `SWARMKIT_CONTEXT_COMPRESSION` | Enable read-side context compression (off by default). |
| `SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES` | Payload size below which compression is skipped. |

**Testing**

| Variable | Purpose |
|---|---|
| `SWARMKIT_MOCK_DELEGATE` | When set to 1, the mock provider delegates to every child so a mock run traverses a multi-agent topology (tests only). |

**MCP + sandbox**

| Variable | Purpose |
|---|---|
| `SWARMKIT_MCP_TIMEOUT` | Per-call MCP timeout in seconds. |
| `SWARMKIT_OAUTH_KEY` | Key that encrypts stored OAuth tokens. Generated into .swarmkit/oauth.key when unset. *(secret)* |
| `SWARMKIT_OAUTH_RUN_WINDOW_S` | How long a run is assumed to take: an OAuth token expiring within it is refreshed first. |
| `SWARMKIT_MCP_RETRIES` | Retries for a failing MCP call. |
| `SWARMKIT_CONTAINER_RUNTIME` | docker \| podman for sandboxed servers. |
| `SWARMKIT_SANDBOX_IMAGE` | Image used to sandbox an MCP server. |
| `SWARMKIT_HARNESS_IMAGE` | Image used to run a harness executor. |
| `SWARMKIT_DISABLE_CONTAINER_SANDBOX` | Run MCP servers on the host instead of in a container. Weakens isolation. |
| `SWARMKIT_DOCS_READER_ALLOW_OUTSIDE` | Let docs-reader read outside its workspace root. Disables path confinement. |

**Fleet**

| Variable | Purpose |
|---|---|
| `SWARMKIT_FLEET_REQUIRE_IDENTITY` | Reject fleet calls that do not present a pinned identity. |
| `SWARMKIT_FLEET_REQUIRE_SIGNED_DEPLOY` | Reject unsigned artifact deploys from a fleet. |

**Telemetry**

| Variable | Purpose |
|---|---|
| `SWARMKIT_OTEL_EXPORTER` | OTLP exporter (otlp \| console). Off when unset. |
| `SWARMKIT_OTEL_ENDPOINT` | OTLP collector endpoint. *(URL; userinfo masked)* |
| `SWARMKIT_OTEL_HEADERS` | Extra OTLP headers. *(secret)* |
| `SWARMKIT_OTEL_API_KEY` | OTLP collector credential. *(secret)* |

**Output**

| Variable | Purpose |
|---|---|
| `SWARMKIT_ENV` | Deployment label reported by serve. |
| `SWARMKIT_VERBOSE` | Verbose CLI output. |
| `SWARMKIT_QUIET` | Suppress non-essential CLI output. |
<!-- END GENERATED: env -->

Provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `GOOGLE_API_KEY`, `GROQ_API_KEY`, `TOGETHER_API_KEY`) and the local runtimes' endpoints (`OLLAMA_BASE_URL`, `RKLLAMA_HOST`, `LLAMA_SERVER_URL`, `OVMS_URL`, `MLX_LM_URL`, `LEMONADE_URL`) are declared by each provider's YAML, not by the runtime: a provider with no key set is not registered, and `swarmkit providers list` says which. See the [model provider reference](model-provider.md). Telemetry variables are explained in [Telemetry configuration](telemetry.md); storage ones in [Storage](storage.md).
