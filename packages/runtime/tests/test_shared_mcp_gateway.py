"""One gateway server per process, one registration per execution — and the isolation still holds.

A server per EXECUTION is what breaks: a process serves roughly three and every later one comes up
bound, audited with its full tool list, and serving nothing. Server churn, teardown, task leaks,
port reuse and a teardown race were each ruled out by measurement, and a delay made it *worse* — so
it is per-uvicorn-instance state, and the first instance in a process is the one that works
(`design/details/shared-mcp-gateway.md`).

The old design got isolation for free by giving each execution a whole server. Sharing one makes
isolation something asserted rather than inherited, so each guarantee is tested here rather than
assumed:

* a token authorises ONE registration, not the gateway;
* a released registration's URL 404s, so it cannot outlive the grant that created it;
* concurrent executions cannot see each other's tools;
* **`agent_id` follows the registration** — a shared server that attributed every call to one agent
  would leave a governance record that is quietly false, which is the failure worth guarding
  hardest here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from swarmkit_runtime.mcp._gateway import GatewayTool, mcp_gateway


class _Manager:
    def get_tool_input_schema(self, *_a: Any) -> dict[str, Any]:
        return {"type": "object"}


def _tool(n: int) -> GatewayTool:
    return GatewayTool(
        name=f"wms__t{n}", server_id="wms", tool_name=f"t{n}", description=f"tool {n}"
    )


async def _list_tools(url: str, token: str) -> list[str]:
    """Connect as a real MCP client and return what this session is offered."""
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.sse import sse_client  # noqa: PLC0415

    async with (
        sse_client(url, headers={"Authorization": f"Bearer {token}"}, timeout=8) as (read, write),
        ClientSession(read, write) as session,
    ):
        await asyncio.wait_for(session.initialize(), 10)
        listing = await asyncio.wait_for(session.list_tools(), 10)
    return sorted(t.name for t in listing.tools)


async def _status(url: str, token: str) -> int:
    async with (
        httpx.AsyncClient(timeout=5) as client,
        client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as response,
    ):
        return response.status_code


# ---- the fault this exists to fix ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_seven_sequential_executions_are_all_served() -> None:
    """The reproduction. Per-execution servers manage three before every later one comes up dead;
    with delays between them, only the first survives."""
    served = []

    for n in range(1, 8):
        async with mcp_gateway([_tool(n)], _Manager(), None, agent_id=f"agent{n}") as gw:  # type: ignore[arg-type]
            served.append(await _list_tools(gw.url, gw.token) == [f"wms__t{n}"])

    assert all(served), f"only {sum(served)}/7 executions were served"


@pytest.mark.asyncio
async def test_a_delay_between_executions_does_not_degrade_it() -> None:
    """A delay used to make it worse, not better — which is what ruled out a teardown race."""
    served = []

    for n in range(1, 4):
        async with mcp_gateway([_tool(n)], _Manager(), None, agent_id="a") as gw:  # type: ignore[arg-type]
            served.append(await _list_tools(gw.url, gw.token) == [f"wms__t{n}"])
        await asyncio.sleep(0.5)

    assert all(served)


# ---- isolation, now asserted rather than inherited ----------------------------------------------


@pytest.mark.asyncio
async def test_an_execution_sees_only_its_own_tools() -> None:
    async with (
        mcp_gateway([_tool(1)], _Manager(), None, agent_id="a") as first,  # type: ignore[arg-type]
        mcp_gateway([_tool(2)], _Manager(), None, agent_id="b") as second,  # type: ignore[arg-type]
    ):
        assert await _list_tools(first.url, first.token) == ["wms__t1"]
        assert await _list_tools(second.url, second.token) == ["wms__t2"]


@pytest.mark.asyncio
async def test_a_token_does_not_work_on_another_registration() -> None:
    """The guarantee that replaces "each execution has its own server". A token authorises one
    registration, checked against the registration the PATH names."""
    async with (
        mcp_gateway([_tool(1)], _Manager(), None, agent_id="a") as first,  # type: ignore[arg-type]
        mcp_gateway([_tool(2)], _Manager(), None, agent_id="b") as second,  # type: ignore[arg-type]
    ):
        assert await _status(second.url, first.token) == 401


@pytest.mark.asyncio
async def test_no_token_is_rejected() -> None:
    async with mcp_gateway([_tool(1)], _Manager(), None, agent_id="a") as gw:  # type: ignore[arg-type]
        assert await _status(gw.url, "not-the-token") == 401


@pytest.mark.asyncio
async def test_a_released_registration_stops_answering() -> None:
    """A URL must not outlive the governance decision that granted its tools."""
    async with mcp_gateway([_tool(1)], _Manager(), None, agent_id="a") as gw:  # type: ignore[arg-type]
        url, token = gw.url, gw.token
        assert await _status(url, token) == 200

    assert await _status(url, token) == 404


@pytest.mark.asyncio
async def test_registration_ids_are_not_guessable() -> None:
    """So a path cannot be walked from a neighbouring execution."""
    async with (
        mcp_gateway([_tool(1)], _Manager(), None, agent_id="a") as first,  # type: ignore[arg-type]
        mcp_gateway([_tool(2)], _Manager(), None, agent_id="b") as second,  # type: ignore[arg-type]
    ):
        assert first.gid != second.gid
        assert len(first.gid) >= 16


@pytest.mark.asyncio
async def test_two_concurrent_registrations_are_independent() -> None:
    """Two harness nodes in one run hold two registrations at once.

    The registrations are made concurrently — the arrangement that would expose shared state — and
    then read one at a time. Driving two live MCP client sessions simultaneously proved the same
    property but was timing-sensitive on a loaded test worker, and a flaky test that guards an
    isolation boundary is worse than a steady one.
    """
    async with (
        mcp_gateway([_tool(1)], _Manager(), None, agent_id="a") as first,  # type: ignore[arg-type]
        mcp_gateway([_tool(2)], _Manager(), None, agent_id="b") as second,  # type: ignore[arg-type]
    ):
        assert first.gid != second.gid
        assert await _list_tools(first.url, first.token) == ["wms__t1"]
        assert await _list_tools(second.url, second.token) == ["wms__t2"]
        # Still both live: releasing one must not have been required for the other to work.
        assert await _status(first.url, first.token) == 200


# ---- the audit record stays true ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_call_is_attributed_to_its_own_registrations_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure worth guarding hardest. A shared server that attributed every call to one agent
    would leave a governance record that is quietly false — and a false attribution is worse than a
    missing one, because it reads as fact.

    Asserted at the governed seam on interleaved registrations, which is the arrangement that would
    expose a server-wide `agent_id`. Deliberately not a full MCP round trip: the property is about
    which agent reaches `governed_mcp_call`, and driving two live sessions to prove it made the test
    minutes long for no extra coverage. The round trip is exercised by the demo.
    """
    from swarmkit_runtime.mcp import _gateway  # noqa: PLC0415

    seen: list[tuple[str, str]] = []

    async def _spy(_manager: Any, _gov: Any, **kw: Any) -> Any:
        seen.append((str(kw["agent_id"]), str(kw["tool_name"])))
        return "ok"

    monkeypatch.setattr(_gateway, "governed_mcp_call", _spy)

    alice = _gateway._Registration("g1", "t1", (_tool(1),), "alice", _Manager(), None)
    bob = _gateway._Registration("g2", "t2", (_tool(2),), "bob", _Manager(), None)

    await alice._call("wms__t1", {})
    await bob._call("wms__t2", {})
    await alice._call("wms__t1", {})

    assert seen == [("alice", "t1"), ("bob", "t2"), ("alice", "t1")]


@pytest.mark.asyncio
async def test_a_registration_serves_only_its_own_tool_names() -> None:
    """A name from another registration is unknown here, not silently proxied."""
    from swarmkit_runtime.mcp import _gateway  # noqa: PLC0415

    alice = _gateway._Registration("g1", "t1", (_tool(1),), "alice", _Manager(), None)

    result = await alice._call("wms__t2", {})

    assert "unknown tool" in str(result[0].text)


# ---- lazy start ---------------------------------------------------------------------------------


def test_no_server_is_started_until_something_registers() -> None:
    """A process that runs no harness node must not open a socket. Lazy start is what provides
    that — NOT stopping when idle, which reproduced the original bug: executions are usually
    sequential, so the count returns to zero between them and the next one starts a fresh server.
    """
    from swarmkit_runtime.mcp import _gateway  # noqa: PLC0415

    fresh = _gateway._SharedGatewayServer("127.0.0.1")

    assert fresh._server is None


def test_release_does_not_stop_the_server() -> None:
    """Stated against the source, because reinstating a stop-when-idle would silently restore a
    server-per-execution and measured 2/7 healthy."""
    from pathlib import Path  # noqa: PLC0415

    src = (Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/mcp/_gateway.py").read_text()

    assert "_stop_if_idle" not in src
