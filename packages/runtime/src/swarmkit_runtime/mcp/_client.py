"""MCP client manager — manages connections to MCP servers.

Supports both transports defined by the workspace schema:
- ``stdio``: local process (npx, uvx, python — most dev MCP servers)
- ``http``: remote HTTP endpoint (hosted services with credentials_ref)

The HTTP transport is implemented over the MCP SDK's ``sse_client`` — that
is an SDK-internal detail of how MCP-over-HTTP framing currently works,
and not a separate transport at the workspace level.

See ``design/details/mcp-client.md``.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult
from swarmkit_schema.models.workspace import McpServer


@dataclass(frozen=True)
class MCPServerConfig:
    """Internal value object resolved from a ``McpServer`` schema entry.

    ``transport`` mirrors the workspace schema (``stdio`` | ``http``).
    For ``stdio`` servers ``command`` is the ``[executable, *args]`` list.
    For ``http`` servers ``endpoint`` is the URL.
    """

    server_id: str
    transport: Literal["stdio", "http"] = "stdio"
    command: list[str] = field(default_factory=list)
    endpoint: str = ""
    env: dict[str, str] | None = None


class MCPClientManager:
    """Manages MCP server connections. One session per server, lazily started."""

    def __init__(
        self,
        servers: dict[str, MCPServerConfig] | None = None,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        self._configs = servers or {}
        self._workspace_root = workspace_root
        self._sessions: dict[str, ClientSession] = {}
        self._stack = AsyncExitStack()

    async def start_all(self) -> None:
        """Eagerly open every configured server's session.

        The MCP SDK's stdio task group must be entered and exited from
        the same asyncio task. Lazy-start works for one-shot tests but
        breaks under LangGraph, where the first ``call_tool`` happens
        inside a child task and ``close_all`` runs in the wrapper task.
        Pre-opening here keeps both halves on the same task.
        """
        for server_id in list(self._configs.keys()):
            await self.get_session(server_id)

    async def get_session(self, server_id: str) -> ClientSession:
        """Get or start a session for the given server."""
        if server_id in self._sessions:
            return self._sessions[server_id]

        config = self._configs.get(server_id)
        if config is None:
            raise LookupError(
                f"MCP server '{server_id}' not configured. "
                f"Available: {sorted(self._configs.keys()) or '(none)'}. "
                f"Add it to workspace.yaml under mcp_servers."
            )

        if config.transport == "http":
            session = await self._start_http(config)
        else:
            session = await self._start_stdio(config)

        self._sessions[server_id] = session
        return session

    async def _start_stdio(self, config: MCPServerConfig) -> ClientSession:
        if not config.command:
            raise ValueError(
                f"MCP server '{config.server_id}' has transport=stdio but no command. "
                f"Add a `command: [...]` list to its workspace.yaml entry."
            )
        env = _resolve_env(config.env)
        cwd = str(self._workspace_root) if self._workspace_root else None
        params = StdioServerParameters(
            command=config.command[0],
            args=list(config.command[1:]),
            env=env,
            cwd=cwd,
        )
        transport = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(*transport))
        await session.initialize()
        return session

    async def _start_http(self, config: MCPServerConfig) -> ClientSession:
        if not config.endpoint:
            raise ValueError(
                f"MCP server '{config.server_id}' has transport=http but no endpoint. "
                f"Add an `endpoint: <url>` to its workspace.yaml entry."
            )
        transport = await self._stack.enter_async_context(sse_client(url=config.endpoint))
        session = await self._stack.enter_async_context(ClientSession(*transport))
        await session.initialize()
        return session

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Call a tool on the named MCP server."""
        session = await self.get_session(server_id)
        return await session.call_tool(tool_name, arguments)

    async def list_tools(self, server_id: str) -> list[dict[str, Any]]:
        """List available tools on a server."""
        session = await self.get_session(server_id)
        result = await session.list_tools()
        return [{"name": t.name, "description": t.description or ""} for t in result.tools]

    async def close_all(self) -> None:
        """Close all sessions and stop all servers."""
        await self._stack.aclose()
        self._sessions.clear()

    @property
    def server_ids(self) -> list[str]:
        return sorted(self._configs.keys())


def _resolve_env(env: dict[str, str] | None) -> dict[str, str] | None:
    """Resolve ``${VAR}`` references in env values from the process environment."""
    if not env:
        return None
    resolved: dict[str, str] = {}
    for key, value in env.items():
        if value.startswith("${") and value.endswith("}"):
            var_name = value[2:-1]
            resolved[key] = os.environ.get(var_name, "")
        else:
            resolved[key] = value
    return resolved


def parse_mcp_servers(servers: list[McpServer] | None) -> dict[str, MCPServerConfig]:
    """Convert the workspace's typed ``mcp_servers`` list into client configs.

    Accepts the value of ``SwarmKitWorkspace.mcp_servers`` (or ``None``).
    The schema's ``allOf`` rules already enforce that stdio entries have a
    command and http entries have an endpoint, so this layer just narrows
    types — it does not re-validate.
    """
    if not servers:
        return {}
    configs: dict[str, MCPServerConfig] = {}
    for server in servers:
        transport: Literal["stdio", "http"] = (
            "http" if server.transport.value == "http" else "stdio"
        )
        configs[server.id] = MCPServerConfig(
            server_id=server.id,
            transport=transport,
            command=list(server.command or []),
            endpoint=server.endpoint or "",
            env=dict(server.env) if server.env else None,
        )
    return configs
