"""Actually start a stdio MCP server and list its tools.

Three MCP regressions shipped in one day — `inputSchema` vs `input_schema`, the moved `FastMCP`
import, and a stderr sink with no `fileno()` — and **every one of them** was at the first step of
the MCP path: spawn the subprocess, complete the handshake, list the tools. None depended on a
model, a topology or a workspace. None was caught, because no test ever spawned a real server.

This is that test. It is deliberately the crudest possible check — if it passes, tools reach
agents; if it fails, nothing else about MCP matters.
"""

from __future__ import annotations

import sys

import pytest
from swarmkit_runtime.mcp._client import MCPClientManager, MCPServerConfig

pytestmark = pytest.mark.asyncio


def _bundled_server() -> MCPServerConfig:
    """The bundled gate-validator, spawned exactly as a workspace would spawn it."""
    return MCPServerConfig(
        server_id="gate-validator",
        transport="stdio",
        command=[sys.executable, "-m", "swarmkit_runtime.gate_validator"],
    )


async def test_a_stdio_server_starts_and_reports_its_tools(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The whole MCP path in one assertion: spawn, handshake, list_tools.

    Fails on `AttributeError: fileno` (the stderr sink), on `'Tool' object has no attribute
    'inputSchema'` (the schema rename), and on a bundled server that cannot import (the FastMCP
    move) — the three ways this broke.
    """
    manager = MCPClientManager({"gate-validator": _bundled_server()}, workspace_root=tmp_path)
    try:
        await manager.start_required({"gate-validator"})
        tools = await manager.list_tools("gate-validator")
    finally:
        await manager.close_all()

    assert tools, "the server started but exposed no tools"
    assert all(t["name"] for t in tools)


async def test_tool_input_schemas_are_cached_on_start(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`_cache_tool_schemas` is where the `inputSchema` rename bit: it runs during start, and its
    failure skipped the server entirely while the run carried on with no tools and exited 0."""
    manager = MCPClientManager({"gate-validator": _bundled_server()}, workspace_root=tmp_path)
    try:
        await manager.start_required({"gate-validator"})
        tools = await manager.list_tools("gate-validator")
        schemas = [manager.get_tool_input_schema("gate-validator", t["name"]) for t in tools]
    finally:
        await manager.close_all()

    assert any(s for s in schemas), "no tool schema was cached — the rename regression"


async def test_a_server_that_dies_at_import_is_skipped_with_its_traceback(  # type: ignore[no-untyped-def]
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A server that dies during import reports only "Connection closed" unless its stderr is
    kept. The tail exists for this; it must also not stop working servers from starting."""
    broken = MCPServerConfig(
        server_id="broken",
        transport="stdio",
        command=[sys.executable, "-c", "raise RuntimeError('boom-from-the-child')"],
    )
    manager = MCPClientManager({"broken": broken}, workspace_root=tmp_path)
    try:
        await manager.start_required({"broken"})
    finally:
        await manager.close_all()

    err = capsys.readouterr().err
    assert "failed to start" in err
    # The point of the tail: the child's own error, not just the transport's symptom.
    assert "boom-from-the-child" in err
