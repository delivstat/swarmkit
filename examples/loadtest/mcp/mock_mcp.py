"""A mock stdio MCP server with a latency knob, for the load test's MCP-heavy shape.

Every tool sleeps ``MOCK_MCP_LATENCY_MS`` (base) +/- ``MOCK_MCP_JITTER_MS`` and returns a canned
result — so the MCP-heavy topology measures connection pressure, tool-call amplification and
governance/audit overhead per call, not a real GitHub/Jira/DB. One server, four tools; the workspace
starts four instances of it (one per "system") to mirror four independent MCP connections.

    MOCK_MCP_LATENCY_MS=50 uv run python examples/loadtest/mcp/mock_mcp.py
"""

from __future__ import annotations

import os
import random
import time

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mock")


def _sleep() -> None:
    base = float(os.environ.get("MOCK_MCP_LATENCY_MS", "0") or 0)
    if base <= 0:
        return
    jitter = float(os.environ.get("MOCK_MCP_JITTER_MS", "0") or 0)
    time.sleep(max(0.0, base + random.uniform(-jitter, jitter)) / 1000.0)


@mcp.tool()
def fetch(query: str) -> str:
    """Fetch a record by query (canned; latency-simulated)."""
    _sleep()
    return f"record for {query!r}: ok"


@mcp.tool()
def search(query: str) -> str:
    """Search for records (canned; latency-simulated)."""
    _sleep()
    return f"3 results for {query!r}"


if __name__ == "__main__":
    mcp.run()
