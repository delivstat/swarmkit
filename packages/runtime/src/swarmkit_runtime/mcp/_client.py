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
import contextlib
import hashlib
import json as _json
import logging
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from typing import TextIO

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult
from swarmkit_schema.models.workspace import McpServer

from swarmkit_runtime._principal import current_principal
from swarmkit_runtime.mcp._credentials import resolve_env, resolve_headers
from swarmkit_runtime.mcp._sdk_compat import tool_input_schema, tool_read_only_hint

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
    #: A key_ref resolved through the workspace ``credentials`` block. For ``http`` it becomes an
    #: ``Authorization: Bearer`` header; for ``stdio`` it is reachable from ``env`` as
    #: ``{credential.<ref>}``. It was in the schema and dropped here at parse time, so the value an
    #: author set never existed by the time anything could have used it.
    credentials_ref: str = ""
    #: Extra HTTP headers for ``transport=http``; values support ``${VAR}`` and ``{credential.…}``.
    headers: dict[str, str] = field(default_factory=dict)
    #: The workspace ``credentials`` block, so a ``credentials_ref`` can be resolved at connect
    #: time rather than at parse time — a secret read early is a secret held longer.
    credentials: dict[str, Any] = field(default_factory=dict)
    #: ``{tool_name: "read"|"write"}`` declared by the workspace. Authoritative over the server's
    #: own annotation, because it is the half the operator controls.
    effects: dict[str, str] = field(default_factory=dict)


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


class _StderrTail:
    """The child's stderr: streamed to ours, with the last few lines kept for the error message.

    Backed by a real OS pipe, because ``errlog`` is handed straight to process creation on both
    SDKs and a subprocess needs a **file descriptor**. The first version of this was a
    ``TextIOBase`` with only ``write``/``flush``, which raised ``AttributeError: fileno`` for every
    server on SDK 2.0 — a diagnostic feature that stopped the thing it was meant to explain from
    starting at all. Nothing caught it because no test spawned a real stdio server; one does now.
    """

    _MAX_LINES = 8

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        self.lines: list[str] = []
        read_fd, write_fd = os.pipe()
        self._write = os.fdopen(write_fd, "w", buffering=1, errors="replace")
        self._read = os.fdopen(read_fd, "r", buffering=1, errors="replace")
        self._pump = threading.Thread(
            target=self._drain, name=f"mcp-stderr-{server_id}", daemon=True
        )
        self._pump.start()

    def _drain(self) -> None:
        """Pass every line through to our stderr, keeping a tail. Ends when the pipe closes."""
        try:
            for line in self._read:
                sys.stderr.write(line)
                if line.strip():
                    self.lines.append(line.rstrip())
                    del self.lines[: -self._MAX_LINES]
        except (ValueError, OSError):  # pragma: no cover - pipe closed under us
            pass

    # The subprocess only needs these three.
    def fileno(self) -> int:
        return self._write.fileno()

    def write(self, text: str) -> int:
        return self._write.write(text)

    def flush(self) -> None:
        self._write.flush()

    def close(self) -> None:
        for handle in (self._write, self._read):
            with contextlib.suppress(ValueError, OSError):
                handle.close()


class MCPClientManager:
    """Manages MCP server connections. One session per server, lazily started."""

    def __init__(
        self,
        servers: dict[str, MCPServerConfig] | None = None,
        *,
        workspace_root: Path | None = None,
        credential_service: Any = None,
    ) -> None:
        self._configs = servers or {}
        self._workspace_root = workspace_root
        self._sessions: dict[str, ClientSession] = {}
        self._tool_schemas: dict[str, dict[str, dict[str, Any]]] = {}
        #: ``{server: {tool: readOnlyHint}}`` from the server's own annotations, when it sends any.
        self._tool_read_only: dict[str, dict[str, bool]] = {}
        self._stderr_tails: dict[str, _StderrTail] = {}
        self._stack = AsyncExitStack()
        # The one task that enters and exits every session's context (see `_on_owner`): the MCP
        # SDK's stdio transport is an anyio task group, and a cancel scope entered in one task
        # cannot be exited from another. Requests, jobs and the serve lifespan all ask this task.
        self._owner: asyncio.Task[None] | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_queue: (
            asyncio.Queue[tuple[Callable[[], Coroutine[Any, Any, Any]], asyncio.Future[Any]] | None]
            | None
        ) = None
        self._tool_cache: dict[str, str] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        #: Resolves credential refs, refreshing OAuth tokens at the point of use. Optional so a
        #: manager built for a test or a workspace with no credentials needs no service.
        self._credential_service: Any = credential_service
        #: What secret each open session was opened with, so a changed one can be detected.
        self._session_credentials: dict[str, str] = {}
        #: One task per session, so a single session can be closed without disturbing the others.
        #:
        #: Two constraints meet here. A shared exit stack made eviction a lie — dropping a session
        #: forgot it while its transport (for stdio, a subprocess) stayed open until shutdown, so
        #: reopening *added* a connection rather than replacing one; harmless at one session per
        #: server, an exhaustion at one per user. But giving each session its own stack on the one
        #: owner task is not enough either: anyio cancel scopes form a per-task stack that must
        #: unwind in LIFO order, and closing the least-recently-used session is by definition out
        #: of order. Doing that corrupts the nesting and cancels unrelated sessions.
        #:
        #: So each session owns a task that enters its contexts, hands back the session, waits to
        #: be told to close, and exits those contexts itself. Every cancel scope is then entered
        #: and exited by the same task, in order, and sessions are independently closable.
        #: ``{key: (task, close_event)}``.
        self._session_closers: dict[str, tuple[asyncio.Task[None], asyncio.Event]] = {}
        #: Monotonic timestamp of each session's last use, for idle eviction and LRU.
        self._session_last_used: dict[str, float] = {}

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

                detail = ""
                tail = self._stderr_tails.get(server_id)
                if tail is not None and tail.lines:
                    detail = "\n  " + "\n  ".join(tail.lines)
                print(
                    f"WARNING: MCP server '{server_id}' failed to start: {exc}. "
                    f"Skipping — skills using this server will be unavailable."
                    f"{detail}",
                    file=sys.stderr,
                )
                self._configs.pop(server_id, None)

    async def _resolved_credential(self, server_id: str) -> str | None:
        """The secret this server should present right now, or None when it needs none.

        Goes through :class:`CredentialService`, so an `oauth` credential is refreshed at the point
        of use — the single behaviour every entry point inherits rather than remembering.
        """
        config = self._configs.get(server_id)
        if config is None or not config.credentials_ref or self._credential_service is None:
            return None
        resolved: str = await self._credential_service.resolve(config.credentials_ref)
        return resolved

    def _session_key(self, server_id: str) -> str:
        """The cache key a session for this server may be stored under.

        A `global` connection presents the same secret to everyone, so one session per server is
        correct and stays keyed by the server id alone — today's behaviour, unchanged.

        A `per-user` connection does not. Its session carries one person's bearer, and the cache
        is consulted before any credential is resolved, so keying it by server id would hand
        Alice's open session to Bob's run and let Bob act as Alice. The resolver cannot prevent
        that: it already returned the right token for each of them. The leak is here, one layer
        below, which is why this is a keying problem and not a resolution one.
        """
        config = self._configs.get(server_id)
        ref = getattr(config, "credentials_ref", "") if config is not None else ""
        if not ref or self._credential_service is None:
            return server_id
        try:
            per_user = bool(self._credential_service.is_per_user(ref))
        except Exception:  # a service that cannot answer is treated as shared: see is_per_user
            per_user = False
        if not per_user:
            return server_id
        # NUL cannot appear in a server id (the schema's identifier pattern) or in an identity
        # string that survived auth, so it cannot be forged into a collision between two owners.
        return f"{server_id}\x00{current_principal() or ''}"

    async def _credential_changed(self, server_id: str) -> bool:
        """Would this server now present a different secret than its open session carries?"""
        config = self._configs.get(server_id)
        if config is None or config.transport != "http" or self._credential_service is None:
            return False
        if not config.credentials_ref:
            return False
        try:
            current = await self._resolved_credential(server_id)
        except Exception:
            return False
        return current is not None and current != self._session_credentials.get(
            self._session_key(server_id)
        )

    async def _drop_session(self, server_id: str) -> None:
        """Close one session and forget it, so the next call reopens it."""
        await self._close_key(self._session_key(server_id))

    async def _close_key(self, key: str) -> None:
        """Close the session stored under *key* and release its transport.

        Dropping a session used to mean forgetting it: the transport stayed open until the manager
        shut down, because every session shared one exit stack. Reopening therefore added a
        connection rather than replacing one — invisible with one session per server, an
        exhaustion once the population is users x servers.

        Closing is recoverable by construction: every consumer calls `get_session` per tool call,
        so a closed session is simply reopened on next use, and a call already in flight holds its
        own session object and finishes on it.
        """
        self._sessions.pop(key, None)
        self._session_credentials.pop(key, None)
        self._session_last_used.pop(key, None)
        closer = self._session_closers.pop(key, None)
        if closer is None:
            return
        task, closing = closer
        closing.set()
        try:
            await task
        except Exception:
            _logger.debug("closing MCP session %r raised", key, exc_info=True)

    async def _spawn_session(self, config: MCPServerConfig) -> ClientSession:
        """Open a session on a task of its own, and leave that task parked until it is closed.

        The task is the unit of lifetime, not the exit stack: anyio requires the cancel scopes a
        transport opens to be exited by the task that entered them, in the order they were entered.
        One task per session satisfies both halves, which a shared owner task cannot once sessions
        are closed out of order.
        """
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[ClientSession] = loop.create_future()
        closing = asyncio.Event()
        opener = self._start_http if config.transport == "http" else self._start_stdio

        async def _hold() -> None:
            try:
                async with AsyncExitStack() as stack:
                    session = await opener(config, stack)
                    if not ready.done():
                        ready.set_result(session)
                    await closing.wait()
            except BaseException as exc:
                if not ready.done():
                    ready.set_exception(exc)
                elif not isinstance(exc, asyncio.CancelledError):
                    _logger.debug("MCP session for %r ended with an error", config.server_id)

        task = loop.create_task(_hold(), name=f"mcp-session:{config.server_id}")
        try:
            session = await ready
        except BaseException:
            closing.set()
            raise
        self._session_closers[self._session_key(config.server_id)] = (task, closing)
        return session

    def _per_user_keys(self, *, stdio_only: bool = False) -> list[str]:
        """Open per-user session keys, least-recently-used first."""
        keys = [k for k in self._sessions if _is_per_user_key(k)]
        if stdio_only:
            keys = [k for k in keys if self._transport_of(k) == "stdio"]
        return sorted(keys, key=lambda k: self._session_last_used.get(k, 0.0))

    def _transport_of(self, key: str) -> str:
        config = self._configs.get(_server_of_key(key))
        return str(getattr(config, "transport", "")) if config is not None else ""

    async def _evict_idle(self) -> None:
        """Close per-user sessions nobody has used for a while.

        Only per-user ones: a global session is one per server and is meant to stay warm. A
        per-user session held open because one person ran one thing this morning is pure cost, and
        for stdio it is a parked subprocess.
        """
        ttl = _idle_ttl_s()
        if ttl <= 0:
            return
        now = time.monotonic()
        for key in self._per_user_keys():
            if now - self._session_last_used.get(key, now) > ttl:
                _logger.info("closing idle per-user MCP session %r", key.split("\x00")[0])
                await self._close_key(key)

    async def _enforce_ceiling(self, config: MCPServerConfig) -> None:
        """Make room for one more per-user session, closing the least recently used.

        Two ceilings, because the two transports do not cost the same. An `http` per-user session
        is a client session; a `stdio` one is a *subprocess per user*, which is rarely what anyone
        intends and is the thing that actually exhausts a host. Reaching a ceiling closes the LRU
        rather than refusing: closing is recoverable (every call re-fetches its session) and
        refusing would fail a run for being unlucky in the ordering.
        """
        stdio = str(getattr(config, "transport", "")) == "stdio"
        limit = _stdio_ceiling() if stdio else _ceiling()
        if limit <= 0:
            return
        keys = self._per_user_keys(stdio_only=stdio)
        while len(keys) >= limit:
            victim = keys.pop(0)
            _logger.info(
                "per-user MCP session ceiling reached (%d, %s); closing least-recently-used %r",
                limit,
                "stdio" if stdio else "http",
                victim.split("\x00")[0],
            )
            await self._close_key(victim)

    # ---- session ownership ----------------------------------------------------------------------

    async def _on_owner(self, fn: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        """Run *fn* on this manager's owner task and return its result.

        The owner task is created lazily on the running loop and lives until ``close_all``. If the
        loop that created it is gone (a test that runs each case under its own ``asyncio.run``),
        a fresh owner is started — the sessions the old one held died with its loop.
        """
        loop = asyncio.get_running_loop()
        if self._owner is None or self._owner.done() or self._owner_loop is not loop:
            self._owner_loop = loop
            self._owner_queue = asyncio.Queue()
            self._stack = AsyncExitStack()
            self._sessions.clear()
            # The stacks belonged to the dead loop's task; their contexts cannot be exited from
            # here and died with it. Forget them rather than leak references to closed transports.
            self._session_closers.clear()
            self._session_last_used.clear()
            self._owner = loop.create_task(self._owner_main(self._owner_queue), name="mcp-owner")
        assert self._owner_queue is not None
        fut: asyncio.Future[Any] = loop.create_future()
        await self._owner_queue.put((fn, fut))
        return await fut

    async def _owner_main(
        self,
        queue: asyncio.Queue[
            tuple[Callable[[], Coroutine[Any, Any, Any]], asyncio.Future[Any]] | None
        ],
    ) -> None:
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                fn, fut = item
                try:
                    result = await fn()
                except BaseException as exc:
                    if not fut.done():
                        fut.set_exception(exc)
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                else:
                    if not fut.done():
                        fut.set_result(result)
        finally:
            # Whatever happens to the owner, the sessions it entered are exited here — the only
            # place they legally can be.
            try:
                await self._stack.aclose()
            except Exception:
                _logger.debug("closing MCP sessions raised", exc_info=True)
            self._sessions.clear()

    async def get_session(self, server_id: str) -> ClientSession:
        """Get or start a session for the given server.

        For an HTTP server the credential is resolved *here*, on every call, rather than once when
        the session opened. A session under `swarmkit serve` can outlive many token lifetimes, and
        a header bound at connect time pins whatever was valid at startup — the refresh would
        update the store and change nothing on the wire (credential-service.md).
        """
        key = self._session_key(server_id)
        if key in self._sessions:
            if await self._credential_changed(server_id):
                # The SDK gives no way to alter an open sse_client's headers, so the session is
                # reopened with the fresh one. Reconnecting is cheap next to a 401 mid-run.
                await self._drop_session(server_id)
            else:
                self._session_last_used[key] = time.monotonic()
                return self._sessions[key]

        config = self._configs.get(server_id)
        if config is None:
            raise LookupError(
                f"MCP server '{server_id}' not configured. "
                f"Available: {sorted(self._configs.keys()) or '(none)'}. "
                f"Add it to workspace.yaml under mcp_servers."
            )

        await self._evict_idle()
        if _is_per_user_key(key):
            await self._enforce_ceiling(config)

        session = await self._spawn_session(config)
        self._sessions[key] = session
        self._session_last_used[key] = time.monotonic()
        return session

    async def _start_stdio(self, config: MCPServerConfig, stack: AsyncExitStack) -> ClientSession:
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
            env = resolve_env(config.env, config.credentials) or _resolve_env(config.env)

        cwd: str | None
        if config.cwd:
            cwd = _expand_var(config.cwd)
        else:
            cwd = _resolve_cwd(resolved_cmd, self._workspace_root, config.sandboxed)
        params = StdioServerParameters(command=cmd, args=args, env=env, cwd=cwd)
        # Tee the child's stderr: it still reaches the terminal, and the last lines are kept so a
        # start failure can quote them. A server that dies during import reports only "Connection
        # closed" — the symptom of a subprocess that hung up, indistinguishable from a hang — while
        # the actual traceback scrolls past somewhere else.
        errlog = _StderrTail(config.server_id)
        self._stderr_tails[config.server_id] = errlog
        # cast: the SDK annotates errlog as TextIO; the process only needs fileno/write/flush,
        # which the pipe-backed sink provides.
        transport = await stack.enter_async_context(
            stdio_client(params, errlog=cast("TextIO", errlog))
        )
        session = await stack.enter_async_context(ClientSession(*transport))
        await session.initialize()
        return session

    async def _start_http(self, config: MCPServerConfig, stack: AsyncExitStack) -> ClientSession:
        if not config.endpoint:
            raise ValueError(
                f"MCP server '{config.server_id}' has transport=http but no endpoint. "
                f"Add an `endpoint: <url>` to its workspace.yaml entry."
            )
        # The declared credentials, actually sent. This was `sse_client(url=...)` with no headers
        # at all, while the schema told authors to put their token in `credentials_ref` — so a
        # remote server configured exactly as documented was called anonymously.
        headers = resolve_headers(
            credentials_ref=config.credentials_ref,
            headers=config.headers,
            credentials=config.credentials,
        )
        # An `oauth` credential is not in the workspace credentials block — it lives in the token
        # store, and resolving it may refresh it first. Resolved here so the header carries a token
        # that is valid *now*, and recorded so a later change is detectable.
        resolved = await self._resolved_credential(config.server_id)
        if resolved:
            headers["Authorization"] = f"Bearer {resolved}"
            self._session_credentials[self._session_key(config.server_id)] = resolved
        transport = await stack.enter_async_context(
            sse_client(url=config.endpoint, headers=headers or None)
        )
        session = await stack.enter_async_context(ClientSession(*transport))
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
        self._tool_schemas[server_id] = {t.name: tool_input_schema(t) for t in result.tools}
        hints = {t.name: hint for t in result.tools if (hint := tool_read_only_hint(t)) is not None}
        self._tool_read_only[server_id] = hints

    def get_effects(self, server_id: str, tool_name: str) -> str:
        """Whether a tool reads or writes: ``"read"``, ``"write"`` or ``"unknown"``.

        Resolution order, and the reason for it:

        1. **The workspace\'s declared ``effects``** wins. It is the only source the person
           configuring the server controls, and the only one that cannot change under them when a
           server is upgraded.
        2. **The server\'s ``readOnlyHint`` annotation**, when it sends one. Useful and free, but
           it is the server describing itself.
        3. **``"unknown"``** otherwise — never a guess. This replaced a substring scan of the tool
           name for ``create|delete|set|add|…`` (issue #825), which denied ``get_dataset`` and
           ``read_asset`` on ``set``, denied ``list_addresses`` on ``add``, and let
           ``truncate_table``, ``purge_cache`` and ``revoke_token`` straight through. The
           vocabulary of destructive verbs is unbounded, so a longer list moves the failure rather
           than removing it.
        """
        cfg = self._configs.get(server_id)
        if cfg is not None:
            declared = cfg.effects.get(tool_name)
            if declared is not None:
                return declared
        hint = self._tool_read_only.get(server_id, {}).get(tool_name)
        if hint is not None:
            return "read" if hint else "write"
        return "unknown"

    def get_tool_input_schema(self, server_id: str, tool_name: str) -> dict[str, Any]:
        """Return the cached ``inputSchema`` for a tool, or an empty schema.

        This is intentionally sync — tool schemas are pre-fetched during
        ``start_all``, so the hot path is a dict lookup.
        """
        server_tools = self._tool_schemas.get(server_id, {})
        return dict(server_tools.get(tool_name, {}))

    async def close_all(self) -> None:
        """Close all sessions and stop all servers.

        Each session parks a task of its own that exits the contexts it entered (`_spawn_session`),
        so they are closed here rather than by the owner: a task cannot unwind another task's
        cancel scopes, which is the same rule that made the owner task necessary in the first
        place.
        """
        for key in list(self._session_closers):
            await self._close_key(key)
        owner, queue = self._owner, self._owner_queue
        self._owner = self._owner_queue = None
        if owner is None or owner.done() or queue is None:
            self._sessions.clear()
            return
        if self._owner_loop is not asyncio.get_running_loop():
            # Another loop's task: its sessions died with that loop; nothing to await here.
            self._sessions.clear()
            return
        await queue.put(None)
        try:
            await owner
        except Exception:
            _logger.debug("MCP owner task ended with an error", exc_info=True)
        self._sessions.clear()

    @property
    def server_ids(self) -> list[str]:
        return sorted(self._configs.keys())


#: Separates a server id from the owner in a per-user session key. Neither a server id (the
#: schema's identifier pattern) nor an identity that survived auth can contain it, so two owners
#: cannot be forged into one key.
_KEY_SEP = "\x00"


def _is_per_user_key(key: str) -> bool:
    return _KEY_SEP in key


def _server_of_key(key: str) -> str:
    return key.split(_KEY_SEP, 1)[0]


def _int_env(name: str, default: int) -> int:
    """A non-numeric or negative value falls back to the default rather than disabling the bound."""
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value >= 0 else default


def _ceiling() -> int:
    """Most per-user http sessions kept open at once. 0 disables the bound."""
    return _int_env("SWARMKIT_PER_USER_SESSION_MAX", 64)


def _stdio_ceiling() -> int:
    """Most per-user stdio sessions — one subprocess per user, so deliberately far smaller."""
    return _int_env("SWARMKIT_PER_USER_STDIO_SESSION_MAX", 8)


def _idle_ttl_s() -> int:
    """Seconds a per-user session may sit unused before it is closed. 0 disables the sweep."""
    return _int_env("SWARMKIT_PER_USER_SESSION_IDLE_S", 900)


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


def parse_mcp_servers(
    servers: list[McpServer] | None, credentials: dict[str, Any] | None = None
) -> dict[str, MCPServerConfig]:
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
            credentials_ref=getattr(server, "credentials_ref", "") or "",
            headers=dict(getattr(server, "headers", None) or {}),
            # Carried, not resolved: the secret is read when the server is started, so a workspace
            # that loads a hundred servers and starts two holds two secrets, not a hundred.
            credentials=dict(credentials or {}),
            effects={
                k: ("read" if getattr(v, "value", v) == "read" else "write")
                for k, v in (getattr(server, "effects", None) or {}).items()
            },
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
