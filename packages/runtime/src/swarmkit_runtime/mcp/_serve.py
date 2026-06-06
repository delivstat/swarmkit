"""SwarmKit MCP Server — expose workspace topologies as MCP tools.

Runs on stdio for use with Claude Desktop, Cursor, Claude Code, etc.
Auto-discovers workspace topologies and exposes each as a callable tool.

Usage:
    swarmkit mcp-serve ./workspace
    swarmkit mcp-serve ./workspace1 ./workspace2

In Claude Desktop config:
    {
      "mcpServers": {
        "swarmkit": {
          "command": "swarmkit",
          "args": ["mcp-serve", "./my-workspace"]
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("swarmkit.mcp-serve")


def run_mcp_server(workspace_paths: list[Path]) -> None:
    """Start the MCP server on stdio, exposing workspace topologies as tools."""
    try:
        from mcp.server import Server  # noqa: PLC0415
        from mcp.server.stdio import stdio_server  # noqa: PLC0415
        from mcp.types import TextContent, Tool  # noqa: PLC0415
    except ImportError:
        print(
            "MCP package required: uv tool install swarmkit-runtime[serve]",
            file=sys.stderr,
        )
        sys.exit(1)

    runtimes, workspaces_info = _load_workspaces(workspace_paths)
    if not runtimes:
        print("No valid workspaces found.", file=sys.stderr)
        sys.exit(1)

    server = Server("swarmkit")
    multi = len(runtimes) > 1

    def _tool_name(ws_id: str, topo_name: str) -> str:
        return f"run_{ws_id}_{topo_name}" if multi else f"run_{topo_name}"

    def _search_tool_name(ws_id: str) -> str:
        return f"search_{ws_id}_knowledge" if multi else "search_knowledge"

    _register_handlers(
        server,
        runtimes,
        workspaces_info,
        _tool_name,
        _search_tool_name,
        TextContent,
        Tool,
    )

    async def _main() -> None:
        async with stdio_server() as (read, write):
            await server.run(
                read,
                write,
                server.create_initialization_options(),
            )

    asyncio.run(_main())


def _load_workspaces(
    workspace_paths: list[Path],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    runtimes: dict[str, WorkspaceRuntime] = {}
    workspaces_info: dict[str, dict[str, Any]] = {}

    for ws_path in workspace_paths:
        resolved = ws_path.resolve()
        if not (resolved / "workspace.yaml").exists():
            logger.warning("No workspace.yaml in %s, skipping", resolved)
            continue
        try:
            rt = WorkspaceRuntime.from_workspace_path(resolved)
            ws_id = rt.workspace.raw.metadata.id
            runtimes[ws_id] = rt
            workspaces_info[ws_id] = {
                "path": str(resolved),
                "topologies": list(rt.workspace.topologies.keys()),
                "skills": [s.id for s in rt.workspace.skills.values()],
            }
            logger.info(
                "Loaded workspace %s: %d topologies",
                ws_id,
                len(rt.workspace.topologies),
            )
        except Exception:
            logger.warning(
                "Failed to load workspace at %s",
                resolved,
                exc_info=True,
            )

    return runtimes, workspaces_info


def _register_handlers(
    server: Any,
    runtimes: dict[str, Any],
    workspaces_info: dict[str, dict[str, Any]],
    tool_name_fn: Any,
    search_name_fn: Any,
    TextContent: type,
    Tool: type,
) -> None:
    @server.list_tools()  # type: ignore[untyped-decorator]
    async def list_tools() -> list[Any]:
        tools: list[Any] = []
        for ws_id, rt in runtimes.items():
            for topo_name, topo in rt.workspace.topologies.items():
                desc = (
                    getattr(topo.raw.metadata, "description", None) or f"Run {topo_name} topology"
                )
                tools.append(
                    Tool(
                        name=tool_name_fn(ws_id, topo_name),
                        description=f"[{ws_id}] {desc}",
                        inputSchema={
                            "type": "object",
                            "required": ["input"],
                            "properties": {
                                "input": {
                                    "type": "string",
                                    "description": "Task or question for the swarm.",
                                },
                            },
                        },
                    )
                )
            if rt._mcp_manager is not None:
                tools.append(
                    Tool(
                        name=search_name_fn(ws_id),
                        description=f"[{ws_id}] Search the workspace knowledge base.",
                        inputSchema={
                            "type": "object",
                            "required": ["query"],
                            "properties": {
                                "query": {"type": "string", "description": "Search query."},
                            },
                        },
                    )
                )
        tools.append(
            Tool(
                name="list_workspaces",
                description="List installed SwarmKit workspaces and their topologies.",
                inputSchema={"type": "object", "properties": {}},
            )
        )
        return tools

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
        if name == "list_workspaces":
            return [
                TextContent(
                    type="text",
                    text=json.dumps(workspaces_info, indent=2),
                )
            ]
        for ws_id, rt in runtimes.items():
            for topo_name in rt.workspace.topologies:
                if name == tool_name_fn(ws_id, topo_name):
                    return await _run_topology(rt, topo_name, arguments, TextContent)
            if name == search_name_fn(ws_id):
                return await _search(rt, arguments, TextContent)
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _run_topology(
    rt: Any,
    topo_name: str,
    arguments: dict[str, Any],
    TextContent: type,
) -> list[Any]:
    user_input = arguments.get("input", "")
    try:
        await rt.start_session()
        result = await rt.run(topo_name, user_input)
        usage_info = ""
        if result.usage:
            usage_info = f"\n\n---\nTokens: {result.usage.total_tokens}"
            if result.usage.by_model:
                parts = [f"{m}: {t}" for m, t in result.usage.by_model.items()]
                usage_info += f" ({', '.join(parts)})"
        return [TextContent(type="text", text=result.output + usage_info)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error running {topo_name}: {e}")]
    finally:
        await rt.end_session()


async def _search(
    rt: Any,
    arguments: dict[str, Any],
    TextContent: type,
) -> list[Any]:
    query = arguments.get("query", "")
    if rt._mcp_manager is None:
        return [TextContent(type="text", text="No MCP servers configured.")]
    try:
        await rt._mcp_manager.start_all()
        tools = await rt._mcp_manager.list_tools()
        search_tools = [t for t in tools if "search" in t.name.lower()]
        if not search_tools:
            return [TextContent(type="text", text="No search tools available.")]
        tool = search_tools[0]
        result = await rt._mcp_manager.call_tool(tool.name, {"query": query})
        return [TextContent(type="text", text=str(result))]
    except Exception as e:
        return [TextContent(type="text", text=f"Search error: {e}")]
