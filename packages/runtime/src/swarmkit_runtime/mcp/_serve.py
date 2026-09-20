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
        from mcp.server.stdio import stdio_server  # noqa: PLC0415
        from mcp.types import TextContent, Tool  # noqa: PLC0415

        from swarmkit_runtime.mcp._sdk_compat import (  # noqa: PLC0415
            build_low_level_server,
        )
    except ImportError:
        print(
            "MCP package required — a base dependency of swarmkit-runtime; reinstall it",
            file=sys.stderr,
        )
        sys.exit(1)

    runtimes, workspaces_info = _load_workspaces(workspace_paths)
    if not runtimes:
        print("No valid workspaces found.", file=sys.stderr)
        sys.exit(1)

    multi = len(runtimes) > 1

    def _tool_name(ws_id: str, topo_name: str) -> str:
        return f"run_{ws_id}_{topo_name}" if multi else f"run_{topo_name}"

    def _search_tool_name(ws_id: str) -> str:
        return f"search_{ws_id}_knowledge" if multi else "search_knowledge"

    _list, _call = _build_handlers(
        runtimes,
        workspaces_info,
        _tool_name,
        _search_tool_name,
        TextContent,
        Tool,
    )
    server = build_low_level_server("swarmkit", list_tools=_list, call_tool=_call)

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


def _build_handlers(
    runtimes: dict[str, Any],
    workspaces_info: dict[str, dict[str, Any]],
    tool_name_fn: Any,
    search_name_fn: Any,
    TextContent: type,
    Tool: type,
) -> tuple[Any, Any]:
    """The tools/list + tools/call handlers, in the SDK-neutral form the compat builder adapts."""

    async def list_tools() -> list[Any]:
        tools: list[Any] = []
        for ws_id, rt in runtimes.items():
            for topo_name, topo in rt.workspace.topologies.items():
                # `hello@0.4.0` is a version key (canary, Level 12), not a tool: `@` and `.` are
                # outside the MCP tool-name alphabet, and the bare name routes to the version the
                # workspace chose.
                if "@" in topo_name:
                    continue
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
                if "@" in topo_name:
                    continue
                if name == tool_name_fn(ws_id, topo_name):
                    return await _run_topology(rt, topo_name, arguments, TextContent)
            if name == search_name_fn(ws_id):
                return await _search(rt, arguments, TextContent)
        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return list_tools, call_tool


#: One job store per runtime for the life of this process. `swarmkit mcp-serve` is long-lived and
#: may hold several workspaces, and a store per call would lose every job the moment it returned.
_JOB_STORES: dict[int, Any] = {}
_SEMAPHORES: dict[int, Any] = {}


def _run_deps(rt: Any) -> tuple[Any, Any, Any, Any]:
    """The job store, durable store, config and capacity gate this runtime's runs go through."""
    import asyncio  # noqa: PLC0415

    from swarmkit_runtime.server._config import ServerCfg  # noqa: PLC0415
    from swarmkit_runtime.server._jobs import JobStore  # noqa: PLC0415

    key = id(rt)
    cfg = ServerCfg()
    if key not in _JOB_STORES:
        _JOB_STORES[key] = JobStore()
        # The same bound `swarmkit serve` applies. Without it this front door had no capacity gate
        # at all: an assistant could start unlimited concurrent runs on a box that bounds every
        # other caller.
        _SEMAPHORES[key] = asyncio.Semaphore(max(1, cfg.max_concurrent))
    store = None
    try:
        store = rt.store
    except Exception:
        store = None
    return _JOB_STORES[key], store, cfg, _SEMAPHORES[key]


async def _run_topology(
    rt: Any,
    topo_name: str,
    arguments: dict[str, Any],
    TextContent: type,
) -> list[Any]:
    """Run a topology for an MCP tool call, through the service every other interface uses.

    This called `WorkspaceRuntime.run` directly, so a run started from an MCP client was not a
    job: no durable row (invisible in `/jobs`, the portal and cost attribution), no canary routing,
    no capacity gate, no `source`. The twin path inside `swarmkit serve` was fixed; this one was
    missed the same way the trigger path once was.
    """
    from swarmkit_runtime.canary import router_for_workspace  # noqa: PLC0415
    from swarmkit_runtime.server._services import JobService, ServiceError  # noqa: PLC0415

    user_input = arguments.get("input", "")
    try:
        await rt.start_session()
        job_store, store, cfg, semaphore = _run_deps(rt)
        try:
            job = await JobService(job_store).start(
                rt=rt,
                canary=router_for_workspace(rt.workspace),
                store=store,
                cfg=cfg,
                semaphore=semaphore,
                topology_name=topo_name,
                user_input=user_input,
                # The same default the serve-side MCP tool and the webhook route use.
                max_steps=10,
                source="mcp",
            )
        except ServiceError as exc:
            return [TextContent(type="text", text=f"Could not start {topo_name}: {exc}")]

        # Await the task rather than polling: a run that dies without a terminal status would spin
        # a poll loop for ever, and polling swallows the exception the caller should see.
        if job.task is not None:
            await job.task
        result = job.result
        if result is None:
            reason = job.error or f"the run ended {job.status}"
            return [TextContent(type="text", text=f"Error running {topo_name}: {reason}")]

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
