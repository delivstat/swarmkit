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

## Recommendation

**Option B** (compiler-level filtering) is the lowest-effort, highest-impact change. SwarmKit already maps skills to specific MCP server tools — extending this to filter the tool registration at compile time would prevent unused tools from bloating context. No new MCP server needed, no protocol changes.

**Option A** (discovery wrapper) is worth building for workspaces with many servers where agents need dynamic exploration — but it's a bigger change and should come after Option B proves the concept.

## Non-goals

- Changing the MCP protocol
- Building a custom tool routing layer outside MCP
- Replacing the existing skill-to-MCP mapping

## Open questions

- Should the discovery pattern be opt-in per topology, or should the compiler always filter unused tools?
- How does this interact with agents that use `skills_additional` (which merges onto archetype defaults)?
- For Option A, should the discovery MCP server be part of the SwarmKit runtime or a separate package?

## References

- [Do we need MCP anymore?](https://corsair.dev/mcp-vs-cli) — benchmark analysis showing discovery-first MCP matches CLI performance
- `design/details/mcp-client.md` — current MCP integration design
