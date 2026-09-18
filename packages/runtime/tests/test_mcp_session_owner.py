"""MCP sessions are owned by one task per manager, so they can be opened from one task and
closed from another — a serve reload, a job, the lifespan — without the transport's task group
refusing ("attempted to exit cancel scope in a different task").

Uses a real stdio MCP server (a five-line FastMCP script), so the anyio task group is the SDK's.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from swarmkit_runtime.mcp._client import MCPClientManager, MCPServerConfig

_SERVER = """\
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("tiny")

@mcp.tool()
def ping(x: str) -> str:
    return "pong " + x

mcp.run()
"""


def _config(tmp_path: Path) -> MCPServerConfig:
    script = tmp_path / "tiny_server.py"
    script.write_text(_SERVER)
    return MCPServerConfig(
        server_id="tiny",
        transport="stdio",
        command=[sys.executable, str(script)],
    )


@pytest.mark.asyncio
async def test_open_in_one_task_close_in_another(tmp_path: Path) -> None:
    manager = MCPClientManager({"tiny": _config(tmp_path)})

    async def opener() -> list[str]:
        await manager.start_required({"tiny"})
        return [t["name"] for t in await manager.list_tools("tiny")]

    tools = await asyncio.create_task(opener())  # opened in a child task…
    assert tools == ["ping"]
    await manager.close_all()  # …closed in this one: what a reload does
    assert manager._sessions == {}
    # and the manager is usable again afterwards (a fresh owner)
    await asyncio.create_task(manager.start_required({"tiny"}))
    assert [t["name"] for t in await manager.list_tools("tiny")] == ["ping"]
    await manager.close_all()


@pytest.mark.asyncio
async def test_a_cancelled_requester_does_not_kill_the_sessions(tmp_path: Path) -> None:
    """The owner task, not the requesting task, holds the sessions — so cancelling a request
    mid-start (a client that went away) does not tear down what other requests use."""
    manager = MCPClientManager({"tiny": _config(tmp_path)})
    await manager.start_required({"tiny"})
    session = await manager.get_session("tiny")

    async def requester() -> None:
        await manager.get_session("tiny")
        await asyncio.sleep(10)

    task = asyncio.create_task(requester())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await manager.get_session("tiny") is session  # still the same live session
    result = await session.call_tool("ping", {"x": "1"})
    assert "pong 1" in str(result.content[0].text)  # type: ignore[union-attr]
    await manager.close_all()
