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

from ._governed import MCPCallDenied, governed_mcp_call

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider

    from ._client import MCPClientManager

# The single MCP server name the harness sees; each workspace tool is a flat tool under it.
_GATEWAY_SERVER_NAME = "swarmkit"
_NAME_SEP = "__"  # flat tool name = "<server>__<tool>"


@dataclass(frozen=True)
class GatewayTool:
    """One workspace MCP tool the gateway re-exposes: the flat name the harness calls, mapped back
    to its real ``(server_id, tool_name)`` for the governed call."""

    name: str
    server_id: str
    tool_name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayHandle:
    """A running gateway: the URL + bearer token to put in the harness's MCP config, and the tools
    it exposes."""

    url: str
    token: str
    tools: tuple[GatewayTool, ...]

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
    token: str | None = None,
) -> AsyncIterator[GatewayHandle]:
    """Serve an SSE MCP server exposing ``tools`` (governed) on an ephemeral port; yield the handle;
    shut down on exit. No tools ⇒ nothing is served (the caller shouldn't wire a config)."""
    import uvicorn  # noqa: PLC0415
    from mcp.server import Server  # noqa: PLC0415
    from mcp.server.sse import SseServerTransport  # noqa: PLC0415
    from mcp.types import TextContent, Tool  # noqa: PLC0415
    from starlette.applications import Starlette  # noqa: PLC0415
    from starlette.requests import Request  # noqa: PLC0415
    from starlette.responses import JSONResponse  # noqa: PLC0415
    from starlette.routing import Mount, Route  # noqa: PLC0415

    bearer = token or secrets.token_urlsafe(24)
    by_name = {t.name: t for t in tools}
    server: Any = Server(_GATEWAY_SERVER_NAME)

    @server.list_tools()  # type: ignore[untyped-decorator]
    async def _list() -> list[Any]:
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema or {"type": "object"},
            )
            for t in tools
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call(name: str, arguments: dict[str, Any]) -> list[Any]:
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
        return _to_content(resp, TextContent)

    sse = SseServerTransport("/messages/")

    def _authed(scope: Any) -> bool:
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        return bool(headers.get("authorization", "") == f"Bearer {bearer}")

    async def _handle_sse(request: Request) -> Any:
        if not _authed(request.scope):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await server.run(r, w, server.create_initialization_options())

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
        url = f"http://{host}:{port}/sse"
        yield GatewayHandle(url=url, token=bearer, tools=tuple(tools))
    finally:
        userver.should_exit = True
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(serve_task, timeout=5)


def _to_content(resp: Any, text_content: type) -> list[Any]:
    """Flatten a ToolResponse into MCP content blocks (text primary)."""
    data = getattr(resp, "data", resp)
    content = getattr(data, "content", None)
    if content:
        out: list[Any] = []
        for block in content:
            text = getattr(block, "text", None)
            if text:
                out.append(text_content(type="text", text=text))
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
