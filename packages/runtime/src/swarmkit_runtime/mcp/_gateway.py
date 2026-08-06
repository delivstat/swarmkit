"""Ephemeral governed MCP gateway for harness executors (executor-mcp-gateway.md, task #23).

For the lifetime of a harness node, SwarmKit stands up a tiny in-process MCP server (SSE transport)
that advertises **only** the tools the agent is granted, and routes every ``call_tool`` through the
one governed path (:func:`~swarmkit_runtime.mcp._governed.governed_mcp_call`) before touching a real
server. The harness points its own MCP config at this gateway, so a harness's tool call is governed
+ audited exactly like a model agent's — never a direct, ungoverned call.

Protected by a per-run bearer token; bound to an ephemeral port; torn down on exit.
"""

from __future__ import annotations

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
    """Serve an SSE MCP server exposing ``tools`` (governed) on an ephemeral port; yield the handle;
    shut down on exit. No tools ⇒ nothing is served (the caller shouldn't wire a config).

    ``host`` is the bind address; ``advertise_host`` (default = ``host``) is the host put in the URL
    the harness connects to — for a container sandbox, bind ``0.0.0.0`` but advertise
    ``host.docker.internal`` so the container reaches the host."""
    import uvicorn  # noqa: PLC0415
    from mcp.server.sse import SseServerTransport  # noqa: PLC0415
    from mcp.types import ImageContent, TextContent, Tool  # noqa: PLC0415
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import JSONResponse, Response  # noqa: PLC0415
    from starlette.routing import Mount, Route  # noqa: PLC0415

    bearer = token or secrets.token_urlsafe(24)
    by_name = {t.name: t for t in tools}
    # Mutable, because the handle is yielded before any session exists — the caller reads these
    # after the run to tell "the agent needed no tools" from "the agent could not reach any".
    counters = {"listed": 0, "called": 0}

    async def _list() -> list[Any]:
        counters["listed"] += 1
        return [
            Tool(
                name=t.name,
                description=t.description,
                # inputSchema= is the WIRE name and correct on both SDKs; the read is what
                # differs, so it goes through the compat accessor.
                inputSchema=tool_input_schema(t) or {"type": "object"},
            )
            for t in tools
        ]

    async def _call(name: str, arguments: dict[str, Any]) -> list[Any]:
        counters["called"] += 1
        tool = by_name.get(name)
        if tool is None:
            return [TextContent(type="text", text=f"unknown tool: {name}")]
        try:
            resp = await governed_mcp_call(
                mcp_manager,
                governance,
                agent_id=agent_id,
                server_id=tool.server_id,
                tool_name=tool.tool_name,
                arguments=arguments,
            )
        except MCPCallDenied as exc:
            return [TextContent(type="text", text=f"DENIED by governance: {exc}")]
        return _to_content(resp, TextContent, ImageContent)

    # 1.x registers these with decorators, 2.0 with constructor kwargs and a different handler
    # shape. The compat builder is the only place that knows which.
    server: Any = build_low_level_server(_GATEWAY_SERVER_NAME, list_tools=_list, call_tool=_call)

    sse = SseServerTransport("/messages/")

    def _authed(scope: Any) -> bool:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        return bool(headers.get("authorization", "") == f"Bearer {bearer}")

    async def _handle_sse(request: Request) -> Any:
        if not _authed(request.scope):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await server.run(r, w, server.create_initialization_options())
        return Response()  # the SSE stream already flushed via request._send; keep Starlette happy

    async def _handle_post(scope: Any, receive: Any, send: Any) -> None:
        if not _authed(scope):
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await sse.handle_post_message(scope, receive, send)

    app = Starlette(
        routes=[Route("/sse", endpoint=_handle_sse), Mount("/messages/", app=_handle_post)]
    )
    config = uvicorn.Config(app, host=host, port=0, log_level="warning", lifespan="off")
    userver = uvicorn.Server(config)
    import asyncio  # noqa: PLC0415

    serve_task = asyncio.create_task(userver.serve())
    try:
        # Wait for uvicorn to bind + report its ephemeral port.
        while not userver.started:
            await asyncio.sleep(0.02)
        port = userver.servers[0].sockets[0].getsockname()[1]
        url = f"http://{advertise_host or host}:{port}/sse"
        # The counters are shared with the running handlers, so the caller can read them after the
        # run and tell "the agent needed no tools" from "no session ever reached the gateway".
        yield GatewayHandle(url=url, token=bearer, tools=tuple(tools), counters=counters)
    finally:
        userver.should_exit = True
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(serve_task, timeout=5)


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
