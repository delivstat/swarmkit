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

import asyncio
import hashlib
import json as _json
import logging
import os
import re
import shutil
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult
from swarmkit_schema.models.workspace import McpServer

PermissionTier = Literal["open", "cautious", "strict", "readonly"]


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
    permission: PermissionTier = "cautious"
    permission_overrides: dict[str, PermissionTier] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolMetadata:
    """Provenance metadata attached to every MCP tool call response.

    Phase A of the MCP gateway path — lightweight envelope that tracks
    which server/tool produced a result, with timing and arguments.
    See ``design/details/structured-inter-agent-communication.md`` Layer 1.
    """

    source: str
    args: dict[str, Any] | None = None
    timestamp: str = ""
    duration_ms: int = 0
    server_id: str = ""


@dataclass(frozen=True)
class ToolResponse:
    """MCP tool result with provenance envelope.

    ``data`` is the original ``CallToolResult`` from the MCP SDK.
    ``metadata`` provides source attribution for downstream consumers
    (structured output ``source`` field, audit logs, observability).
    """

    data: CallToolResult
    metadata: ToolMetadata


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
        self._tool_cache: dict[str, str] = {}
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def configs(self) -> dict[str, MCPServerConfig]:
        """The resolved MCP server configs, keyed by id (read-only view for reachability checks)."""
        return dict(self._configs)

    async def start_all(self) -> None:
        """Eagerly open every configured server's session.

        Prefer ``start_required()`` when you have a topology — it only
        starts the servers the topology actually needs.

        The MCP SDK's stdio task group must be entered and exited from
        the same asyncio task. Lazy-start works for one-shot tests but
        breaks under LangGraph, where the first ``call_tool`` happens
        inside a child task and ``close_all`` runs in the wrapper task.
        Pre-opening here keeps both halves on the same task.

        Servers that fail to start (missing deps, bad command, etc.)
        are skipped with a warning — the run continues without them.
        """
        await self._start_servers(set(self._configs.keys()))

    async def start_required(self, required_server_ids: set[str]) -> None:
        """Start only the MCP servers in *required_server_ids*.

        Servers not in the set are left unstarted — no process spawned,
        no connection opened. This reduces startup latency and resource
        usage for workspaces with many MCP servers when the topology
        only references a few.

        Unknown server IDs (not in ``_configs``) are silently ignored.
        """
        known = required_server_ids & set(self._configs.keys())
        await self._start_servers(known)

    async def _start_servers(self, server_ids: set[str]) -> None:
        """Start sessions and cache tool schemas for the given server IDs."""
        for server_id in sorted(server_ids):
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

        resolved_cmd = list(config.command)
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

    def get_permission(self, server_id: str, tool_name: str) -> PermissionTier:
        """Resolve the effective permission tier for a server+tool.

        Per-tool overrides take precedence over the server default.
        Returns ``"cautious"`` if the server is not configured.
        """
        cfg = self._configs.get(server_id)
        if cfg is None:
            return "cautious"
        override = cfg.permission_overrides.get(tool_name)
        if override is not None:
            return override
        return cfg.permission

    def get_server_cwd(self, server_id: str) -> str | None:
        """Return the resolved cwd for a server, or ``None``."""
        cfg = self._configs.get(server_id)
        if cfg and cfg.cwd:
            return _expand_var(cfg.cwd)
        if self._workspace_root:
            return str(self._workspace_root)
        return None

    @staticmethod
    def _cache_key(server_id: str, tool_name: str, arguments: dict[str, Any] | None) -> str:
        raw = _json.dumps(
            {"s": server_id, "t": tool_name, "a": arguments or {}},
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get_cached_result(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None,
    ) -> str | None:
        """Return cached tool result, or None if not cached."""
        key = self._cache_key(server_id, tool_name, arguments)
        return self._tool_cache.get(key)

    def cache_result(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None,
        result: str,
    ) -> None:
        """Cache a successful tool result."""
        key = self._cache_key(server_id, tool_name, arguments)
        self._tool_cache[key] = result

    def clear_cache(self) -> None:
        """Clear the tool result cache."""
        self._tool_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    @property
    def cache_stats(self) -> dict[str, int]:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._tool_cache),
        }

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResponse:
        """Call a tool on the named MCP server.

        Returns a ``ToolResponse`` with the raw ``CallToolResult`` plus
        provenance metadata (source, timing, server_id). Every MCP call
        passes through this method — provenance is automatic.
        """
        session = await self.get_session(server_id)
        start = time.monotonic()
        # Bounded: a wedged MCP server must not hang the whole run indefinitely (model calls
        # already have with_retry timeouts; MCP tool calls had none). Tunable via env.
        timeout = float(os.environ.get("SWARMKIT_MCP_TIMEOUT", "120"))
        try:
            result = await asyncio.wait_for(session.call_tool(tool_name, arguments), timeout)
        except TimeoutError as exc:
            raise RuntimeError(
                f"MCP tool '{server_id}:{tool_name}' timed out after {timeout:.0f}s"
            ) from exc
        elapsed_ms = int((time.monotonic() - start) * 1000)
        metadata = ToolMetadata(
            source=f"{server_id}:{tool_name}",
            args=arguments,
            timestamp=datetime.now(tz=UTC).isoformat(),
            duration_ms=elapsed_ms,
            server_id=server_id,
        )
        return ToolResponse(data=result, metadata=metadata)

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


_logger = logging.getLogger(__name__)

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

    def _replace(m: re.Match[str]) -> str:
        var_name = m.group(1)
        if var_name not in os.environ:
            _logger.warning(
                "Undefined environment variable '${%s}' — expanding to empty string",
                var_name,
            )
            return ""
        return os.environ[var_name]

    return re.sub(r"\$\{([^}]+)\}", _replace, value)


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

    Handles both full-value references (``${VAR}``) and embedded references
    (``${VAR}/suffix``).  Multiple references in a single value are supported.

    When ``inherit`` is True (default, for stdio subprocesses), the parent
    process environment is inherited so that PATH, HOME, NODE_PATH, etc.
    are available to commands like npx. When False (for Docker sandboxed
    commands), only the explicitly declared vars are returned.
    """
    if not env:
        return None
    resolved: dict[str, str] = dict(os.environ) if inherit else {}
    for key, value in env.items():
        resolved[key] = _expand_env_value(value, key)
    return resolved


def _expand_env_value(value: str, env_key: str) -> str:
    """Expand ``${VAR}`` references in a single env value, warning on undefined vars."""

    def _replace(m: re.Match[str]) -> str:
        var_name = m.group(1)
        if var_name not in os.environ:
            _logger.warning(
                "Undefined environment variable '${%s}' in env key '%s'"
                " — expanding to empty string",
                var_name,
                env_key,
            )
            return ""
        return os.environ[var_name]

    return re.sub(r"\$\{([^}]+)\}", _replace, value)


_VALID_TIERS: set[str] = {"open", "cautious", "strict", "readonly"}


def _extract_permission(raw: object) -> PermissionTier:
    """Coerce a schema-generated Permission enum (or string) to PermissionTier."""
    if raw is None:
        return "cautious"
    val = getattr(raw, "value", None) or str(raw)
    if val in _VALID_TIERS:
        return val  # type: ignore[return-value]
    return "cautious"


def _extract_permission_overrides(raw: object) -> dict[str, PermissionTier]:
    """Coerce schema-generated permission_overrides to a typed dict."""
    if not raw:
        return {}
    result: dict[str, PermissionTier] = {}
    items: dict[str, Any] = raw if isinstance(raw, dict) else dict(raw)  # type: ignore[call-overload]
    for k, v in items.items():
        val = getattr(v, "value", None) or str(v)
        if val in _VALID_TIERS:
            result[k] = val  # type: ignore[assignment]
    return result


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
        permission = _extract_permission(getattr(server, "permission", None))
        overrides = _extract_permission_overrides(getattr(server, "permission_overrides", None))
        configs[server.id] = MCPServerConfig(
            server_id=server.id,
            transport=transport,
            command=list(server.command or []),
            endpoint=server.endpoint or "",
            env=dict(server.env) if server.env else None,
            cwd=server.cwd or "",
            sandboxed=bool(server.sandboxed) if server.sandboxed is not None else False,
            sandbox_image=server.sandbox_image or "",
            permission=permission,
            permission_overrides=overrides,
        )
    return configs


def collect_required_servers(topology: Any) -> set[str]:
    """Walk a resolved topology's agent tree and return the set of MCP server IDs needed.

    Inspects each agent's skills — skills with ``implementation.type == "mcp_tool"``
    reference a server via ``implementation.server``. Only those server IDs are returned.

    Accepts a ``ResolvedTopology`` (avoiding a hard import to keep this module
    import-light).
    """
    from swarmkit_runtime.skills import impl_get  # noqa: PLC0415

    server_ids: set[str] = set()

    def _walk(agent: Any) -> None:
        for skill in agent.skills:
            impl = skill.raw.implementation
            if impl_get(impl, "type") == "mcp_tool":
                sid = impl_get(impl, "server")
                if sid:
                    server_ids.add(str(sid))
        for child in agent.children:
            _walk(child)

    _walk(topology.root)
    return server_ids
