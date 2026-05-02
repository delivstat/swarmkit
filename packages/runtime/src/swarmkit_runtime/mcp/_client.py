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
import shutil
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
    cwd: str = ""
    sandboxed: bool = False
    sandbox_image: str = ""


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
        self._tool_schemas: dict[str, dict[str, dict[str, Any]]] = {}
        self._stack = AsyncExitStack()

    async def start_all(self) -> None:
        """Eagerly open every configured server's session.

        The MCP SDK's stdio task group must be entered and exited from
        the same asyncio task. Lazy-start works for one-shot tests but
        breaks under LangGraph, where the first ``call_tool`` happens
        inside a child task and ``close_all`` runs in the wrapper task.
        Pre-opening here keeps both halves on the same task.

        Servers that fail to start (missing deps, bad command, etc.)
        are skipped with a warning — the run continues without them.
        """
        for server_id in list(self._configs.keys()):
            try:
                await self.get_session(server_id)
                await self._cache_tool_schemas(server_id)
            except Exception as exc:
                import sys  # noqa: PLC0415

                print(
                    f"WARNING: MCP server '{server_id}' failed to start: {exc}. "
                    f"Skipping — skills using this server will be unavailable.",
                    file=sys.stderr,
                )
                self._configs.pop(server_id, None)

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

        if config.sandboxed:
            cmd, args, env = _build_sandboxed_command(config, workspace_root=self._workspace_root)
        else:
            resolved_cmd = [_expand_var(part) for part in config.command]
            cmd = resolved_cmd[0]
            args = resolved_cmd[1:]
            env = _resolve_env(config.env)

        cwd: str | None
        if config.cwd:
            cwd = _expand_var(config.cwd)
        else:
            cwd = _resolve_cwd(resolved_cmd, self._workspace_root, config.sandboxed)
        params = StdioServerParameters(command=cmd, args=args, env=env, cwd=cwd)
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

    async def _cache_tool_schemas(self, server_id: str) -> None:
        """Fetch and cache every tool's ``inputSchema`` for a server."""
        if server_id in self._tool_schemas:
            return
        session = await self.get_session(server_id)
        result = await session.list_tools()
        self._tool_schemas[server_id] = {
            t.name: dict(t.inputSchema) if t.inputSchema else {} for t in result.tools
        }

    def get_tool_input_schema(self, server_id: str, tool_name: str) -> dict[str, Any]:
        """Return the cached ``inputSchema`` for a tool, or an empty schema.

        This is intentionally sync — tool schemas are pre-fetched during
        ``start_all``, so the hot path is a dict lookup.
        """
        server_tools = self._tool_schemas.get(server_id, {})
        return dict(server_tools.get(tool_name, {}))

    async def close_all(self) -> None:
        """Close all sessions and stop all servers."""
        await self._stack.aclose()
        self._sessions.clear()

    @property
    def server_ids(self) -> list[str]:
        return sorted(self._configs.keys())


_SANDBOX_IMAGE = os.environ.get("SWARMKIT_SANDBOX_IMAGE", "swarmkit-mcp-sandbox")


def _build_sandboxed_command(
    config: MCPServerConfig,
    *,
    workspace_root: Path | None = None,
) -> tuple[str, list[str], dict[str, str] | None]:
    """Wrap an MCP server command in ``docker run`` for process isolation.

    The container runs with ``--network=none`` (no outbound access),
    ``--rm`` (auto-cleanup), and the workspace mounted read-only at
    ``/workspace``. Environment variables from the config are passed
    via ``-e`` flags after ``${VAR}`` expansion.

    Returns ``(command, args, env)`` suitable for ``StdioServerParameters``.
    The env is ``None`` because variables are injected into the container
    via ``-e``, not the host process.
    """
    if not shutil.which("docker"):
        raise RuntimeError(
            f"MCP server '{config.server_id}' has sandboxed=true but "
            f"'docker' is not on PATH. Install Docker or set sandboxed=false."
        )

    docker_args = [
        "run",
        "-i",
        "--rm",
        "--network=none",
    ]

    if workspace_root is not None:
        docker_args.extend(["-v", f"{workspace_root}:/workspace:ro", "-w", "/workspace"])

    resolved_env = _resolve_env(config.env, inherit=False)
    if resolved_env:
        for key, value in resolved_env.items():
            docker_args.extend(["-e", f"{key}={value}"])

    image = config.sandbox_image or _SANDBOX_IMAGE
    docker_args.append(image)
    docker_args.extend(config.command)

    return "docker", docker_args, None


def _expand_var(value: str) -> str:
    """Expand ``${VAR}`` references in a single string value."""
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], "")
    return value


def _resolve_cwd(
    resolved_cmd: list[str],
    workspace_root: Path | None,
    sandboxed: bool,
) -> str | None:
    """Determine cwd for the MCP server subprocess.

    If the command has a resolved directory path as an argument (like
    the filesystem server), use that as cwd so "." resolves to the
    server's target directory. Otherwise use the workspace root so
    local scripts (uv run script.py) can be found.
    """
    if sandboxed:
        return None
    # Check if any command argument is an existing absolute directory
    for arg in reversed(resolved_cmd[1:]):
        if arg.startswith("/") and os.path.isdir(arg):
            return arg
    return str(workspace_root) if workspace_root else None


def _resolve_env(env: dict[str, str] | None, *, inherit: bool = True) -> dict[str, str] | None:
    """Resolve ``${VAR}`` references in env values from the process environment.

    When ``inherit`` is True (default, for stdio subprocesses), the parent
    process environment is inherited so that PATH, HOME, NODE_PATH, etc.
    are available to commands like npx. When False (for Docker sandboxed
    commands), only the explicitly declared vars are returned.
    """
    if not env:
        return None
    resolved: dict[str, str] = dict(os.environ) if inherit else {}
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
            cwd=server.cwd or "",
            sandboxed=bool(server.sandboxed) if server.sandboxed is not None else False,
            sandbox_image=server.sandbox_image or "",
        )
    return configs
