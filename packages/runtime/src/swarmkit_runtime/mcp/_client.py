"""MCP client manager — manages connections to MCP servers.

See ``design/details/mcp-client.md``.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration for a single MCP server from workspace.yaml."""

    server_id: str
    command: str
    args: list[str]
    env: dict[str, str] | None = None


class MCPClientManager:
    """Manages MCP server connections. One session per server, lazily started."""

    def __init__(self, servers: dict[str, MCPServerConfig] | None = None) -> None:
        self._configs = servers or {}
        self._sessions: dict[str, ClientSession] = {}
        self._stack = AsyncExitStack()
        self._started = False

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

        env = _resolve_env(config.env)
        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=env,
        )

        transport = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(*transport))
        await session.initialize()

        self._sessions[server_id] = session
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
    """Resolve ${VAR} references in env values from the process environment."""
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


def parse_mcp_servers(workspace_config: dict[str, Any]) -> dict[str, MCPServerConfig]:
    """Parse mcp_servers block from workspace config into MCPServerConfig objects."""
    raw = workspace_config.get("mcp_servers", {})
    configs: dict[str, MCPServerConfig] = {}
    for server_id, server_conf in raw.items():
        if not isinstance(server_conf, dict):
            continue
        configs[server_id] = MCPServerConfig(
            server_id=server_id,
            command=server_conf.get("command", ""),
            args=server_conf.get("args", []),
            env=server_conf.get("env"),
        )
    return configs
