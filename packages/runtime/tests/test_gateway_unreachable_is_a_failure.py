"""A harness that reached none of its granted tools did not succeed.

Bug 18. A per-execution MCP gateway started, registered 33 tools, recorded them in
`executor.mcp_gateway` — and then served none of them: its SSE endpoint raised an ASGI protocol
error on every connection. The agent's own probes came back empty, it read the sandbox, correctly
worked out that no tools from the `swarmkit` server were exposed, and wrote nine "no source access"
stubs.

The execution was recorded ``status: success``. The stubs were well-formed JSON satisfying
`output_schema` — citing the `README.md` and the MCP config file the agent could actually see — so
every downstream check passed, and (with bug 17) that output replaced a complete, correct artifact
from the prior execution.

The agent behaved well: it refused to invent content and said plainly that it had no access. Had it
been less careful, the run would have produced confident, fabricated documentation with a full set
of citations and a success status.

The signal is **`listed`, not `called`**. An MCP client asks for the tool list at session init, so
it is non-zero for any session that connected, while an agent that legitimately needed no tool would
still have listed them. Counting calls would fail honest runs; counting lists catches exactly the
sessions that never arrived.

This does not fix the ASGI error itself — that is not reproducible from outside and changing
streaming semantics on a hypothesis is worse than catching the class. It makes the failure loud
wherever it comes from.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.mcp._gateway import GatewayHandle, GatewayTool, mcp_gateway

TOOLS = (
    GatewayTool(name="wms__get_table", server_id="wms", tool_name="get_table", description="d"),
    GatewayTool(name="wms__find_col", server_id="wms", tool_name="find_col", description="d"),
)


class _Manager:
    def get_tool_input_schema(self, *_a: Any) -> dict[str, Any]:
        return {"type": "object"}


def _handle(listed: int = 0, called: int = 0) -> GatewayHandle:
    return GatewayHandle(
        url="http://127.0.0.1:1/sse",
        token="t",
        tools=TOOLS,
        counters={"listed": listed, "called": called},
    )


# ---- what "reached" means ----------------------------------------------------------------------


def test_a_gateway_no_session_touched_was_not_reached() -> None:
    """The bug's state: tools advertised, nothing served."""
    assert _handle(listed=0).reached is False


def test_listing_alone_counts_as_reached() -> None:
    """An agent may legitimately decide it needs no tool. It still listed them, so it saw the
    surface — counting CALLS would fail that honest run."""
    assert _handle(listed=1, called=0).reached is True


def test_calls_are_reported_but_do_not_decide() -> None:
    handle = _handle(listed=1, called=7)

    assert handle.called == 7
    assert handle.reached is True


# ---- the counters are live ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_fresh_gateway_has_not_been_reached() -> None:
    """The handle is yielded before any session exists, so this must start false rather than
    default to a comfortable true."""
    async with mcp_gateway(list(TOOLS), _Manager(), None, agent_id="a") as gw:  # type: ignore[arg-type]
        assert gw.reached is False
        assert gw.listed == 0


@pytest.mark.asyncio
async def test_a_real_mcp_session_marks_the_gateway_reached() -> None:
    """Driven with a real MCP client against the running SSE endpoint, so the counter is proven
    end to end rather than against a stub — the failing run's gateway is exactly the thing that
    could not get this far."""
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.sse import sse_client  # noqa: PLC0415

    async with mcp_gateway(list(TOOLS), _Manager(), None, agent_id="a") as gw:  # type: ignore[arg-type]
        assert gw.reached is False

        async with (
            sse_client(gw.url, headers={"Authorization": f"Bearer {gw.token}"}) as (r, w),
            ClientSession(r, w) as session,
        ):
            await session.initialize()
            listing = await session.list_tools()

        assert {t.name for t in listing.tools} == {"wms__get_table", "wms__find_col"}
        assert gw.reached is True, "a session that listed the tools has reached the gateway"


# ---- the node turns it into a failure -----------------------------------------------------------


def test_the_node_fails_a_run_that_never_reached_its_gateway() -> None:
    """Stated against the source: the whole defect was that this path reported success, so the
    absence of the check is the bug."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "gateway.reached" in src, "a run that reached no tool must not be reported successful"
    assert "executor.mcp_unreachable" in src, "and it must say so in the audit record"


def test_the_check_only_applies_when_a_gateway_was_wired() -> None:
    """An agent with no MCP grants has no gateway, and must not be failed for not reaching one."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "gateway is not None" in src


def test_usage_is_recorded_for_a_healthy_run() -> None:
    """So "advertised 33, called 0, but connected" is visible too — the near-miss of this bug."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "executor.mcp_usage" in src
