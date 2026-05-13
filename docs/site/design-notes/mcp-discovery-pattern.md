---
title: MCP discovery pattern — lean tool surfaces
description: Replace tool-dump MCP servers with discovery-first architecture. Agents discover capabilities at runtime instead of loading all tools into context upfront.
tags: [runtime, mcp, architecture, m8]
status: draft
---

# MCP discovery pattern

**Scope:** runtime, MCP integration
**Design reference:** §18 (MCP integration), `design/details/mcp-client.md`
**Status:** draft — implement as part of M8

## Problem

When a workspace has many MCP servers (Sterling has 9 servers with 30+ tools), every tool is registered at startup and appears in the agent's tool list. This bloats the LLM context window, increases cost, and can confuse the model — more tools means more opportunities for the model to pick the wrong one.

The article [Do we need MCP anymore?](https://corsair.dev/mcp-vs-cli) demonstrates that MCP's poor benchmark results stem from this tool-dump pattern, not from the protocol itself. When implementations use discovery-first architecture, MCP performs comparably to CLI.

## Insight

There should be no relationship between the number of integrations and the number of MCP tools the agent sees. Tools should serve two purposes:

1. **Introspection** — discover what's available
2. **Execution** — run a specific capability

This mirrors how CLI's `--help` works: you don't see every subcommand's full argument spec in the prompt. You discover, then execute.

## Proposed pattern

### Current (tool-dump)

```
Agent context:
  tools: [search_docs, get_schema, list_schemas, get_design_note,
          list_design_notes, list_reference_skills, validate_workspace,
          get_error_reference, write_workspace_file, read_workspace_file,
          run_pytest, search_text, search_text_by_file, get_api_details,
          get_api_input_xml, get_api_output_xml, get_service_config,
          render_service_graph, list_services, list_pipelines, ...]
  # 30+ tools → bloated context, confused model
```

### Proposed (discovery-first)

```
Agent context:
  tools: [discover_capabilities, execute_tool]
  # 2 tools → lean context, model discovers what it needs

discover_capabilities(category="search")
→ [
    {name: "search_docs", server: "knowledge", description: "..."},
    {name: "search_text", server: "fts5", description: "..."},
    {name: "search_configs", server: "cdt", description: "..."},
  ]

execute_tool(server="knowledge", tool="search_docs", args={query: "topology"})
→ [results]
```

## Implementation options

### Option A: Discovery MCP wrapper

A new MCP server that wraps all registered servers and exposes only `discover` + `execute`:

```yaml
mcp_servers:
  - id: discovery
    transport: stdio
    command: ["swarmkit", "mcp-discovery"]
    wraps: [knowledge, fts5, cdt, chromadb, github]
```

The runtime registers only the discovery server's tools with the agent. The discovery server proxies calls to underlying servers.

### Option B: Compiler-level tool batching

The compiler groups tools by category and only loads the relevant batch:

- Agent with `skills: [search-docs]` gets search tools only
- Agent with `skills: [github-pr-read]` gets GitHub tools only
- No agent sees all tools from all servers

This is partially what SwarmKit already does via skill-to-MCP mapping — skills declare which server and tool they use. The improvement would be to not register unused tools at all.

### Option C: Agent-driven lazy loading

The agent starts with a `list_available_servers` tool. When it needs a specific server, it calls `load_server_tools(server_id)` which dynamically adds that server's tools to its available set.

## Patterns from Corsair (open-source reference)

[Corsair](https://github.com/corsairdev/corsair) is an open-source "integration layer for agents" (Apache 2.0, TypeScript). Their architecture solves the same problem and has proven patterns worth adopting:

### 1. Single MCP connection, many integrations

Instead of N separate MCP servers each registering their tools, Corsair exposes one MCP endpoint that proxies to all integrations. Agents call `corsair.slack.api.messages.post()` — namespace-based routing, single connection.

**SwarmKit equivalent:** a `swarmkit mcp-gateway` that wraps all workspace MCP servers behind one connection:

```yaml
mcp_servers:
  - id: gateway
    transport: stdio
    command: ["swarmkit", "mcp-gateway"]
    # wraps all other servers declared in workspace
```

Agent sees: `gateway.github.get_pr`, `gateway.chromadb.search`, `gateway.cdt.get_service` — one tool namespace instead of 30 separate tool registrations.

### 2. Permission tiers per integration

Corsair implements four tiers: `open`, `cautious` (recommended), `strict`, `readonly`. Individual endpoints within an integration can override the tier.

**SwarmKit equivalent:** extend `mcp_servers:` config:

```yaml
mcp_servers:
  - id: github
    transport: stdio
    command: [...]
    permission: cautious    # reads immediate, writes need approval
    overrides:
      delete_branch: strict  # this specific tool needs explicit approval
```

This maps to SwarmKit's governance layer — `evaluate_action` already gates tool calls. The permission tier would set the default governance policy per-server, with per-tool overrides.

### 3. Approval as a database row

When an action needs human approval, Corsair creates a DB record the agent cannot access or modify. The agent blocks until the record is approved externally. The agent cannot bypass the permission by retrying or rephrasing.

**SwarmKit already has this:** the HITL review queue + `HITLDeferredError` + `--resume` flow. The NotificationStore (SQLite) is the equivalent of Corsair's approval database. The key difference: Corsair enforces it at the integration layer, SwarmKit enforces it at the governance layer. Both approaches work — SwarmKit's is more flexible because governance policies can be changed per-workspace.

### 4. Dynamic tool filtering by permissions

Tools are computed at request time based on tenant permissions, not statically enumerated at startup. The agent only sees tools it has permission to call.

**SwarmKit equivalent:** at compile time, the compiler reads the agent's IAM scopes and only registers MCP tools the agent is allowed to call. An agent with `scopes: [code:read]` only sees read tools, not write/delete tools from the same server.

## What already works

**Per-agent tool filtering is already implemented.** The compiler's `_build_tools()` (in `_compiler.py`) creates `ToolSpec` entries only for the agent's own skills. An agent with `skills: [github-pr-read]` sees only that tool plus delegation tools for children. The "all 30 tools to every agent" scenario described in the Problem section does not actually happen in SwarmKit.

## What's missing

1. **Eager server startup** — `MCPClientManager.start_all()` launches every MCP server configured in the workspace, even if the current topology only uses 2 of 9 servers. Wasted processes + startup latency.
2. **No permission tiers** — every MCP tool call goes through the same governance path. No way to say "GitHub reads are auto-approved, writes need human approval."
3. **No gateway** — each server is a separate child process. With 9 servers that's 9 processes.

## Recommendation

**PR 1 (M8):** Lazy server startup. `start_all()` becomes `start_required(topology)`. Scan topology agents' skills, collect referenced server IDs, only start those. Zero new infrastructure, immediate resource savings.

**PR 2 (M8):** Permission tiers on MCP servers. `permission: open|cautious|strict|readonly` per server in workspace.yaml with per-tool overrides. Maps to governance `evaluate_action`.

**PR 3 (M8, if time):** MCP gateway prototype — single process wrapping all workspace servers with namespace routing, inspired by Corsair's single-connection model.

## Non-goals

- Changing the MCP protocol
- Building a custom tool routing layer outside MCP
- Replacing the existing skill-to-MCP mapping
- Full multi-tenancy (Corsair supports this for SaaS — SwarmKit doesn't need it for single-user workspaces)

## Open questions

- Should the discovery pattern be opt-in per topology, or should the compiler always filter unused tools?
- How does this interact with agents that use `skills_additional` (which merges onto archetype defaults)?
- For the MCP gateway, should it be part of the SwarmKit runtime or a separate package?
- Should permission tiers be a workspace-level config or per-topology?

## References

- [Corsair](https://github.com/corsairdev/corsair) — open-source agent integration layer (Apache 2.0)
- [Do we need MCP anymore?](https://corsair.dev/mcp-vs-cli) — benchmark analysis showing discovery-first MCP matches CLI performance
- `design/details/mcp-client.md` — current SwarmKit MCP integration design
