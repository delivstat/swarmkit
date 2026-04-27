---
title: MCP client integration
description: How Swael connects to MCP servers for capability skill execution. Client lifecycle, governance gating, server registry.
tags: [mcp, skills, capability, m5]
status: proposed
---

# MCP client integration

## Goal

Capability skills declared as `implementation.type: mcp_tool` connect
to real MCP servers and call tools. Every MCP call is gated through
`GovernanceProvider.evaluate_action` before execution. MCP servers are
declared in the workspace config and managed by the runtime.

## Non-goals

- **Building MCP servers.** Swael is a client. Community MCP servers
  (7,260+ available) provide the tools.
- **Sandboxed server lifecycle.** Docker-based MCP server supervision
  (§8.8) is a follow-up. For M5, servers are started externally.
- **MCP resources/prompts.** Only MCP tools are used in M5. Resources
  and prompt templates are future.

## Architecture

```
Skill YAML declares: server=filesystem, tool=read_file
                ↓
Compiler resolves: workspace.yaml → mcp_servers.filesystem → stdio command
                ↓
Runtime: MCPClientManager.get_session("filesystem") → ClientSession
                ↓
Governance: evaluate_action(agent_id, "mcp:call", scopes={fs:read})
                ↓
MCP call: session.call_tool("read_file", {path: "src/main.py"})
                ↓
Result flows back through the skill executor → agent node
```

## Workspace configuration

The workspace schema (`packages/schema/schemas/workspace.schema.json`)
is the source of truth. `mcp_servers` is an array of typed entries:

```yaml
# workspace.yaml
mcp_servers:
  - id: filesystem
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
  - id: qdrant
    transport: stdio
    command: ["uvx", "mcp-server-qdrant"]
    env:
      QDRANT_URL: "http://localhost:6333"
      COLLECTION_NAME: "knowledge"
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
    credentials_ref: github_pat
  - id: rynko-flow
    transport: http
    endpoint: https://mcp.rynko.dev
```

Each server entry has:
- `id` — referenced from `skill.implementation.server`
- `transport` — `stdio` (local subprocess) or `http` (remote endpoint)
- `command` — required when `transport=stdio`. The first element is the
  executable, the rest are arguments
- `endpoint` — required when `transport=http`. The HTTP URL of the
  remote MCP service
- `env` — environment variables for stdio servers. Values support
  `${VAR}` expansion from the runtime process environment. Use `env`
  for non-secret configuration; use `credentials_ref` for secrets
- `credentials_ref` — names a `credentials:` entry the workspace
  resolves through the SecretsProvider before injecting into the server
- `sandboxed` — when `true`, forces Docker-or-equivalent isolation
  (design §8.8). Sandbox lifecycle is M5+ — `false` is the only
  supported value today

Stdio servers are launched with `cwd` set to the workspace root, so
script paths in `command` resolve relative to the workspace.yaml
location rather than the user's invocation directory. This is what
lets the on-ramp example reference `hello_world_server.py` directly.

## MCPClientManager

Manages MCP server connections. One `ClientSession` per server, reused
across agent calls.

```python
class MCPClientManager:
    def __init__(
        self,
        servers: dict[str, MCPServerConfig] | None = None,
        *,
        workspace_root: Path | None = None,
    ) -> None: ...
    async def start_all(self) -> None
    async def get_session(self, server_id: str) -> ClientSession
    async def list_tools(self, server_id: str) -> list[ToolInfo]
    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult
    async def close_all(self) -> None
```

Sessions are started via `stdio_client(StdioServerParameters(...))` for
stdio entries and `sse_client(...)` for `http` entries (the SDK still
implements MCP-over-HTTP framing as SSE — that is an SDK-internal detail
and not surfaced as a separate transport at the workspace level).

`start_all` is the entry-point the CLI uses before invoking the topology
graph: the MCP SDK's anyio task groups must be entered and exited from
the same asyncio task, and lazy-start broke under LangGraph because the
first `call_tool` happened in a child task while `close_all` ran in the
wrapper task. Pre-opening from the wrapper keeps both halves co-tasked.
`get_session` remains available for callers that don't need the
constraint (single-shot tests, scripts).

## Governance gating

Every MCP tool call goes through `evaluate_action` before execution:

```python
decision = await governance.evaluate_action(
    agent_id=agent_id,
    action=f"mcp:call:{server_id}:{tool_name}",
    scopes_required=frozenset(skill_scopes),
)
if not decision.allowed:
    return f"DENIED: {decision.reason}"
```

The skill's `iam.required_scopes` defines what scopes are needed.
The agent's `iam.base_scope` must include them. This is the existing
scope-check mechanism — no new governance logic.

## Skill executor wiring

In `_skill_executor.py`, the `mcp_tool` branch calls the manager:

```python
if impl_type == "mcp_tool":
    server_id = impl["server"]
    tool_name = impl["tool"]
    result = await mcp_manager.call_tool(server_id, tool_name, arguments)
    return result.content[0].text  # simplified
```

The `mcp_manager` is passed through the compiler's `compile_topology`
function, similar to `model_provider` and `governance`.

## Error handling

- **Server not found at compile time:** the CLI's `_missing_mcp_servers`
  check walks `workspace.skills` and rejects any `mcp_tool` skill whose
  `server` is not in `mcp_servers`. The user sees a single targeted
  message naming the skill and the missing server before any subprocess
  is launched.
- **Manager not configured:** if a non-CLI caller compiles the topology
  with `mcp_manager=None` while a skill targets `mcp_tool`, the executor
  returns a string naming the missing server and the file to fix
  (`workspace.yaml`).
- **Server won't start:** stdio process fails → `MCPClientManager`
  raises, the skill executor catches and returns an error message,
  execution continues (other agents unaffected).
- **Tool call fails:** MCP returns error → logged via
  `GovernanceProvider.record_event`, error propagated to agent.
- **Server dies mid-run:** connection drops → manager detects, logs,
  returns error for that call. No crash propagation.

## Implementation plan

### PR 1 (this PR): design note + MCPClientManager + mcp_tool wiring

- Design note (this document)
- `mcp/_client.py` — MCPClientManager
- Wire `mcp_tool` in `_skill_executor.py`
- Update `compile_topology` to accept `mcp_manager`
- Tests with a mock MCP session

### PR 2: workspace mcp_servers config

- Parse `mcp_servers` from workspace.yaml
- Build MCPClientManager from workspace config in CLI
- Validate server references at topology load time

### PR 3: Knowledge Curator design + reference skills

- `design/details/knowledge-curator.md` (task #52)
- Reference skills: filesystem-read, qdrant-query
- KB governance (PII/secrets filtering)

## Test plan

- **Mock MCP session.** call_tool returns expected result, skill
  executor produces correct output.
- **Governance deny.** Agent lacks required scopes → MCP call blocked,
  policy.denied event recorded.
- **Server not configured.** Skill references unknown server → clear
  error message.
- **Tool call error.** MCP returns error → error propagated, execution
  continues.
