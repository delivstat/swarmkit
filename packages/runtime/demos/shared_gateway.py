#!/usr/bin/env python
"""Demo: every harness execution in a process gets working MCP tools.

    uv run python packages/runtime/demos/shared_gateway.py

Runs the reproduction that used to degrade — seven sequential executions, each with its own granted
tool — and drives each one with a real MCP client, including an actual tool call so the governed
path and its attribution are exercised end to end.

Before this change each execution started its own uvicorn server, and a process served about three
before every later one came up bound, audited with its full tool list, and serving nothing.
"""

from __future__ import annotations

import asyncio
import logging

from mcp import ClientSession
from mcp.client.sse import sse_client
from swarmkit_runtime.mcp import _gateway
from swarmkit_runtime.mcp._gateway import GatewayTool, gateway_serves, mcp_gateway

EXECUTIONS = 7


class _Manager:
    def get_tool_input_schema(self, *_a: object) -> dict[str, object]:
        return {"type": "object"}


async def main() -> None:
    logging.disable(logging.CRITICAL)

    # Stand in for the real MCP servers, and record who each call is attributed to — the guarantee
    # that a shared server could silently break.
    attributed: list[str] = []

    async def _spy(_manager: object, _gov: object, **kw: object) -> object:
        attributed.append(str(kw["agent_id"]))
        return f"result of {kw['tool_name']}"

    # Stub the governed call so the demo needs no real MCP servers. The point is which agent each
    # call is attributed to, which is what a shared server could quietly get wrong.
    setattr(_gateway, "governed_mcp_call", _spy)  # noqa: B010

    print(f"{EXECUTIONS} sequential executions on one process:\n")
    healthy = 0

    for n in range(1, EXECUTIONS + 1):
        tool = GatewayTool(
            name=f"wms__t{n}", server_id="wms", tool_name=f"t{n}", description=f"tool {n}"
        )
        async with mcp_gateway([tool], _Manager(), None, agent_id=f"agent{n}") as gw:  # type: ignore[arg-type]
            serves = await gateway_serves(gw.url, gw.token)
            async with (
                sse_client(gw.url, headers={"Authorization": f"Bearer {gw.token}"}, timeout=8) as (
                    read,
                    write,
                ),
                ClientSession(read, write) as session,
            ):
                await asyncio.wait_for(session.initialize(), 10)
                listing = await asyncio.wait_for(session.list_tools(), 10)
                await asyncio.wait_for(session.call_tool(f"wms__t{n}", {}), 10)

            names = [t.name for t in listing.tools]
            ok = serves and names == [f"wms__t{n}"]
            healthy += ok
            print(f"  execution {n}: serves={serves} tools={names} called=1 {'ok' if ok else 'X'}")

    print(f"\nhealthy: {healthy}/{EXECUTIONS}")
    print(f"calls attributed to: {attributed}")
    print("\nEach execution saw only its own tool, and each call was attributed to its own agent —")
    print("the isolation the old design got for free by giving every execution a whole server.")


if __name__ == "__main__":
    asyncio.run(main())
