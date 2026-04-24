---
title: MCP client integration
description: How SwarmKit connects to MCP servers for capability skill execution. Client lifecycle, governance gating, server registry.
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

- **Building MCP servers.** SwarmKit is a client. Community MCP servers
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

```yaml
# workspace.yaml
mcp_servers:
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
  qdrant:
    command: uvx
    args: ["mcp-server-qdrant"]
    env:
      QDRANT_URL: "http://localhost:6333"
      COLLECTION_NAME: "knowledge"
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
```

Each server entry has:
- `command` + `args` — the stdio command to launch the server
- `env` — environment variables (supports `${VAR}` expansion from
  process environment)

## MCPClientManager

Manages MCP server connections. One `ClientSession` per server,
lazily initialized on first use, reused across agent calls.

```python
class MCPClientManager:
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

Sessions are started via `stdio_client(StdioServerParameters(...))`.
The manager holds the context managers and cleans up on shutdown.

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

- **Server not found:** skill references `server: github` but
  workspace has no `mcp_servers.github` → clear error at topology
  load time (resolver can check this).
- **Server won't start:** stdio process fails → `MCPClientManager`
  returns error, skill executor returns error message, execution
  continues (other agents unaffected).
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
