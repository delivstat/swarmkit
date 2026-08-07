"""Ephemeral governed MCP gateway for harness executors (executor-mcp-gateway.md, task #23).

For the lifetime of a harness node, SwarmKit stands up a tiny in-process MCP server (SSE transport)
that advertises **only** the tools the agent is granted, and routes every ``call_tool`` through the
one governed path (:func:`~swarmkit_runtime.mcp._governed.governed_mcp_call`) before touching a real
server. The harness points its own MCP config at this gateway, so a harness's tool call is governed
+ audited exactly like a model agent's — never a direct, ungoverned call.

Protected by a per-run bearer token; bound to an ephemeral port; torn down on exit.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from swarmkit_runtime.mcp._sdk_compat import build_low_level_server, tool_input_schema

from ._governed import MCPCallDenied, governed_mcp_call

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider

    from ._client import MCPClientManager

# The single MCP server name the harness sees; each workspace tool is a flat tool under it.
#: The MCP server name SwarmKit registers with the harness. Part of the same contract — Claude
#: Code exposes a gateway tool as ``mcp__<this>__<flat name>``.
GATEWAY_SERVER_NAME = "swarmkit"
_GATEWAY_SERVER_NAME = GATEWAY_SERVER_NAME
#: Flat gateway tool name = ``<server><SEP><tool>``. Public because the name is a CONTRACT, not an
#: internal detail: a harness adapter has to reconstruct it to write a tool grant the harness will
#: match, and a grant written in any other namespace silently matches nothing.
GATEWAY_NAME_SEP = "__"
_NAME_SEP = GATEWAY_NAME_SEP


@dataclass(frozen=True)
class GatewayTool:
    """One workspace MCP tool the gateway re-exposes: the flat name the harness calls, mapped back
    to its real ``(server_id, tool_name)`` for the governed call."""

    name: str
    server_id: str
    tool_name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayHandle:
    """A running gateway: the URL + bearer token to put in the harness's MCP config, and the tools
    it exposes."""

    url: str
    token: str
    tools: tuple[GatewayTool, ...]
    #: Live serving counters, owned by the running gateway. Read after the run.
    counters: dict[str, int] = field(default_factory=lambda: {"listed": 0, "called": 0})
    #: This execution's registration on the shared server. Released on exit, after which the URL
    #: 404s — a path must not outlive the governance decision that granted its tools.
    gid: str = ""

    @property
    def listed(self) -> int:
        """How many times a session asked for the tool list. An MCP client lists at session init,
        so this is non-zero for ANY session that connected — independent of whether the agent then
        chose to call anything."""
        return self.counters["listed"]

    @property
    def called(self) -> int:
        """Tool calls served. Zero can be legitimate — an agent may simply not need one."""
        return self.counters["called"]

    @property
    def reached(self) -> bool:
        """Whether any session actually saw the tool surface.

        False with tools advertised means no session ever reached the gateway: the harness ran with
        none of its granted tools, and reported success.
        """
        return self.listed > 0

    def harness_config(self) -> dict[str, Any]:
        """The harness-native MCP config (Claude Code shape) pointing at this gateway."""
        return harness_mcp_config(self.url, self.token)


def build_gateway_tools(
    granted: Iterable[tuple[str, str, str]], mcp_manager: MCPClientManager
) -> list[GatewayTool]:
    """Build the gateway's tool surface from the agent's granted ``(server_id, tool_name,
    description)`` triples — the input schema comes from the manager's pre-fetched cache. Deduped by
    flat name, sorted for a stable surface."""
    seen: dict[str, GatewayTool] = {}
    for server_id, tool_name, description in granted:
        if not server_id or not tool_name:
            continue
        flat = f"{server_id}{_NAME_SEP}{tool_name}"
        if flat in seen:
            continue
        seen[flat] = GatewayTool(
            name=flat,
            server_id=server_id,
            tool_name=tool_name,
            description=description or f"{tool_name} on {server_id}",
            input_schema=(
                mcp_manager.get_tool_input_schema(server_id, tool_name) or {"type": "object"}
            ),
        )
    return [seen[k] for k in sorted(seen)]


async def gateway_serves(url: str, token: str, timeout: float = 5.0) -> bool:
    """Whether this gateway actually serves its SSE endpoint.

    Bound and a recorded tool list are not evidence that it works. A gateway can come up, report a
    port, be audited with all its tools — and then serve nothing, which is how a harness came to run
    with none of its 33 granted tools while reporting success. The only proof is the endpoint event
    an MCP client receives on connect, so that is what this asks for.

    Cheap (one connection, closed immediately) and worth it: the alternative to knowing here is
    finding out after a full harness session has been paid for.
    """
    import httpx  # noqa: PLC0415

    try:
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as response,
        ):
            if response.status_code != 200:
                return False
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    return True
    except Exception:  # a gateway that cannot be probed is a gateway that cannot be used
        return False
    return False


def harness_mcp_config(url: str, token: str) -> dict[str, Any]:
    """The Claude-Code-shaped MCP config that points a harness at the gateway (one SSE server,
    bearer-authenticated). Other harnesses declare their own consumption in their adapter."""
    return {
        "mcpServers": {
            _GATEWAY_SERVER_NAME: {
                "type": "sse",
                "url": url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }


class _Registration:
    """One execution's slice of the shared gateway: its own transport, MCP server, tools, token,
    agent id and counters. Everything except the HTTP server is per-execution, which is where the
    isolation lives now that the server itself is shared."""

    def __init__(
        self,
        gid: str,
        token: str,
        tools: tuple[GatewayTool, ...],
        agent_id: str,
        mcp_manager: Any,
        governance: Any,
    ) -> None:
        from mcp.server.sse import SseServerTransport  # noqa: PLC0415

        self.gid = gid
        self.token = token
        self.tools = tools
        self.agent_id = agent_id
        self.counters: dict[str, int] = {"listed": 0, "called": 0}
        self._by_name = {t.name: t for t in tools}
        self.transport = SseServerTransport(f"/gw/{gid}/messages/")
        self.server: Any = build_low_level_server(
            _GATEWAY_SERVER_NAME, list_tools=self._list, call_tool=self._call
        )
        self._mcp_manager = mcp_manager
        self._governance = governance

    async def _list(self) -> list[Any]:
        from mcp.types import Tool  # noqa: PLC0415

        self.counters["listed"] += 1
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=tool_input_schema(t) or {"type": "object"},
            )
            for t in self.tools
        ]

    async def _call(self, name: str, arguments: dict[str, Any]) -> list[Any]:
        from mcp.types import ImageContent, TextContent  # noqa: PLC0415

        self.counters["called"] += 1
        tool = self._by_name.get(name)
        if tool is None:
            return [TextContent(type="text", text=f"unknown tool: {name}")]
        try:
            resp = await governed_mcp_call(
                self._mcp_manager,
                self._governance,
                # The REGISTRATION's agent, never a server-wide one. A shared server that attributed
                # every call to one agent would leave a governance record that is quietly false —
                # the failure this design flagged as the one worth guarding hardest.
                agent_id=self.agent_id,
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                arguments=arguments,
            )
        except MCPCallDenied as exc:
            return [TextContent(type="text", text=f"DENIED by governance: {exc}")]
        return _to_content(resp, TextContent, ImageContent)


class _SharedGatewayServer:
    """One uvicorn server per (process, bind host), shared by every execution.

    A server per EXECUTION is what breaks: a process serves roughly three and every later one comes
    up bound, audited with its full tool list, and serving nothing — measured, with churn, teardown,
    task leaks, port reuse and a teardown race each ruled out, and a delay making it worse rather
    than better (`design/details/shared-mcp-gateway.md`). The first instance in a process works, so
    there is exactly one.

    Reference-counted: starts on the first registration, stops on the last, so a process that runs
    no harness node never opens a socket.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._regs: dict[str, _Registration] = {}
        self._server: Any = None
        self._task: Any = None
        self._port = 0
        self._loop: Any = None
        self._lock = asyncio.Lock()

    def _lookup(self, path: str) -> _Registration | None:
        parts = [p for p in path.split("/") if p]
        return self._regs.get(parts[1]) if len(parts) > 1 and parts[0] == "gw" else None

    @staticmethod
    def _bearer(headers: Any) -> str:
        found = {k.decode().lower(): v.decode() for k, v in headers}.get("authorization", "")
        return str(found)

    def _authed(self, reg: _Registration | None, scope: Any) -> bool:
        """A token authorises ONE registration, not the gateway. Checked against the registration
        the path names, so a token minted for A is rejected on B's path."""
        return reg is not None and self._bearer(scope.get("headers", [])) == f"Bearer {reg.token}"

    def _build_app(self) -> Any:
        from starlette.applications import Starlette  # noqa: PLC0415
        from starlette.responses import JSONResponse, Response  # noqa: PLC0415
        from starlette.routing import Mount, Route  # noqa: PLC0415

        class _AlreadyStreamed(Response):
            """A Response that writes nothing new, because the SSE handler already streamed.

            Starlette's request-response route requires a Response back, and returning a real one
            sends a SECOND `http.response.start` on a connection `connect_sse` already began — the
            ASGI protocol error seen in the wild. Sending NOTHING is not the fix either: the
            response never completes and teardown blocks on it. A terminal empty body ends the
            exchange cleanly.
            """

            async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
                with contextlib.suppress(RuntimeError):
                    await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def sse_endpoint(request: Any) -> Any:
            reg = self._lookup(request.scope["path"])
            if reg is None:
                # A released registration 404s: a URL must not outlive the grant that created it.
                return JSONResponse({"error": "unknown gateway"}, status_code=404)
            if not self._authed(reg, request.scope):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            async with reg.transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read, write):
                await reg.server.run(read, write, reg.server.create_initialization_options())
            return _AlreadyStreamed()

        async def post_endpoint(scope: Any, receive: Any, send: Any) -> None:
            reg = self._lookup(scope["path"])
            if reg is None:
                await JSONResponse({"error": "unknown gateway"}, status_code=404)(
                    scope, receive, send
                )
                return
            if not self._authed(reg, scope):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
            await reg.transport.handle_post_message(scope, receive, send)

        return Starlette(
            routes=[Route("/gw/{gid}/sse", endpoint=sse_endpoint), Mount("/gw", app=post_endpoint)]
        )

    async def _ensure_started(self) -> None:
        # A running server belongs to the event loop that started it. The registry is process-wide,
        # so a SECOND loop in the same process — a later `asyncio.run`, another test — would find a
        # server marked started whose serve task is on a loop that has closed, hand out its URL, and
        # serve nothing: the original bug, reintroduced through the back door. Bound to its loop,
        # and replaced when the loop changes.
        loop = asyncio.get_running_loop()
        if self._server is not None and self._loop is loop and not loop.is_closed():
            return
        if self._server is not None:
            self._server, self._task, self._port = None, None, 0
            self._regs.clear()
        import uvicorn  # noqa: PLC0415

        config = uvicorn.Config(
            self._build_app(), host=self._host, port=0, log_level="warning", lifespan="off"
        )
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.02)
        self._server, self._task, self._loop = server, task, loop
        self._port = server.servers[0].sockets[0].getsockname()[1]

    async def register(
        self,
        tools: Sequence[GatewayTool],
        mcp_manager: Any,
        governance: Any,
        *,
        agent_id: str,
        advertise_host: str | None,
    ) -> GatewayHandle:
        async with self._lock:
            await self._ensure_started()
            # Unguessable, so a path cannot be walked from a neighbouring execution.
            gid = secrets.token_urlsafe(16)
            reg = _Registration(
                gid, secrets.token_urlsafe(24), tuple(tools), agent_id, mcp_manager, governance
            )
            self._regs[gid] = reg
            url = f"http://{advertise_host or self._host}:{self._port}/gw/{gid}/sse"
            return GatewayHandle(
                url=url, token=reg.token, tools=reg.tools, counters=reg.counters, gid=gid
            )

    async def release(self, gid: str) -> None:
        """Drop the registration. The SERVER stays up for the life of the process.

        Stopping it when idle was the first version of this, and it reproduced the very bug the
        design exists to fix: executions are usually SEQUENTIAL, so the count returns to zero
        between them, the server stops, and the next execution starts a fresh one — a server per
        execution again, measured at 2/7 healthy. Lazy start already gives the property that
        mattered (a process with no harness node never opens a socket); eagerly stopping gives
        nothing and costs everything.
        """
        async with self._lock:
            self._regs.pop(gid, None)


#: One shared server per bind host. Keyed, because a container sandbox needs `0.0.0.0` while a
#: local one uses loopback, and a server already bound to loopback cannot serve the container.
_SERVERS: dict[str, _SharedGatewayServer] = {}


@asynccontextmanager
async def mcp_gateway(
    tools: Sequence[GatewayTool],
    mcp_manager: MCPClientManager,
    governance: GovernanceProvider | None,
    *,
    agent_id: str,
    host: str = "127.0.0.1",
    advertise_host: str | None = None,
    token: str | None = None,
) -> AsyncIterator[GatewayHandle]:
    """Register this execution on the process's shared gateway; release it on exit.

    Same contract as before — the caller gets a URL and a bearer token exposing exactly the tools it
    was granted, governed and audited. What changed is underneath: the HTTP server is shared and
    only the registration is per-execution, because a server per execution is what stops working
    after the first few in a process (`design/details/shared-mcp-gateway.md`).
    """
    server = _SERVERS.setdefault(host, _SharedGatewayServer(host))
    handle = await server.register(
        tools, mcp_manager, governance, agent_id=agent_id, advertise_host=advertise_host
    )
    try:
        yield handle
    finally:
        await server.release(handle.gid)


def _to_content(resp: Any, text_content: type, image_content: type | None = None) -> list[Any]:
    """Flatten a ToolResponse into MCP content blocks — text AND images.

    This used to read ``.text`` off every block and keep only what had one. ``ImageContent`` carries
    ``.data`` (base64) + ``.mimeType`` and no ``.text``, so every image was skipped; a response that
    was *only* images left ``out`` empty and fell through to ``str(data)``, delivering the repr
    ``Image: screen1.png (.png, 14960 bytes base64)``.

    The tool call succeeded, the bytes were read, and the picture was discarded in the last step
    before the harness saw it — while the trace showed a green tool call. An agent asked to describe
    a screenshot could then only stall or fabricate.

    A **model** node has always rendered these correctly (`_skill_executor` builds a real image
    block). The gateway is the harness's only route to MCP, so the same skill on the same workspace
    worked on a model node and silently degraded on a harness. Matching the model path here is what
    makes an executor an implementation detail rather than a capability difference.
    """
    data = getattr(resp, "data", resp)
    content = getattr(data, "content", None)
    if content:
        out: list[Any] = []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                out.append(text_content(type="text", text=text))
                continue
            # Same discriminator the model path uses, rather than sniffing for attributes: a text
            # block with an empty string should not be mistaken for an image.
            if image_content is not None and getattr(block, "type", None) == "image":
                b64 = getattr(block, "data", None)
                mime = getattr(block, "mimeType", None)
                if b64 and mime:
                    out.append(image_content(type="image", data=b64, mimeType=mime))
        if out:
            return out
    return [text_content(type="text", text=str(data))]


__all__ = [
    "GatewayHandle",
    "GatewayTool",
    "build_gateway_tools",
    "harness_mcp_config",
    "mcp_gateway",
]
