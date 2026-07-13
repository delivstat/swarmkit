"""Ephemeral governed MCP gateway (executor-mcp-gateway.md, task #23).

Unit-covers the tool surface + config generation, then an integration test that starts the real SSE
gateway and drives it with SwarmKit's own MCP client — proving list+call work over HTTP and that
every call is governed (no live harness needed).
"""

from __future__ import annotations

# The doubles duck-type MCPClientManager / GovernanceProvider without inheriting them.
# mypy: disable-error-code="arg-type"
from typing import Any

import pytest
from swarmkit_runtime.governance import AuditEvent, PolicyDecision
from swarmkit_runtime.mcp._gateway import (
    GatewayTool,
    build_gateway_tools,
    harness_mcp_config,
    mcp_gateway,
)


class _Resp:
    def __init__(self, text: str) -> None:
        self.data = type("D", (), {"content": [type("B", (), {"text": text})()]})()


class _Mgr:
    def __init__(self, permission: str = "cautious") -> None:
        self._perm = permission
        self._schemas = {("fs", "read"): {"type": "object", "properties": {"path": {}}}}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def get_permission(self, server_id: str, tool_name: str) -> str:
        return self._perm

    def get_tool_input_schema(self, server_id: str, tool_name: str) -> dict[str, Any]:
        return self._schemas.get((server_id, tool_name), {})

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> _Resp:
        self.calls.append((server_id, tool_name, arguments))
        return _Resp(f"read {arguments}")


class _Gov:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed
        self.actions: list[str] = []

    async def evaluate_action(self, *, action: str, **_: object) -> PolicyDecision:
        self.actions.append(action)
        return PolicyDecision(allowed=self._allowed, reason="" if self._allowed else "no", tier=1)

    async def record_event(self, event: AuditEvent) -> None: ...


# --- pure pieces --------------------------------------------------------------------------------


def test_build_gateway_tools_filters_dedups_and_pulls_schema() -> None:
    mgr = _Mgr()
    tools = build_gateway_tools(
        [
            ("fs", "read", "Read a file"),
            ("fs", "read", "dup"),  # deduped
            ("", "x", "no server"),  # dropped
            ("search", "web", ""),  # empty description → synthesized
        ],
        mgr,
    )
    names = [t.name for t in tools]
    assert names == ["fs__read", "search__web"]  # sorted, deduped, filtered
    fs = next(t for t in tools if t.name == "fs__read")
    assert fs.server_id == "fs" and fs.tool_name == "read"
    assert fs.input_schema == {"type": "object", "properties": {"path": {}}}


def test_harness_mcp_config_shape() -> None:
    cfg = harness_mcp_config("http://127.0.0.1:9/sse", "tok123")
    server = cfg["mcpServers"]["swarmkit"]
    assert server["type"] == "sse"
    assert server["url"] == "http://127.0.0.1:9/sse"
    assert server["headers"]["Authorization"] == "Bearer tok123"


# --- integration: real SSE gateway driven by SwarmKit's MCP client ------------------------------


@pytest.mark.asyncio
async def test_gateway_serves_and_governs_over_sse() -> None:
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.sse import sse_client  # noqa: PLC0415

    mgr, gov = _Mgr("cautious"), _Gov(allowed=True)
    tools = [GatewayTool("fs__read", "fs", "read", "Read a file", {"type": "object"})]
    async with mcp_gateway(tools, mgr, gov, agent_id="coder") as gw:
        headers = {"Authorization": f"Bearer {gw.token}"}
        async with (
            sse_client(gw.url, headers=headers) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert [t.name for t in listed.tools] == ["fs__read"]
            result = await session.call_tool("fs__read", {"path": "app.py"})
            text = result.content[0].text  # type: ignore[union-attr]
    # the call went through governance + reached the (fake) real server
    assert gov.actions == ["mcp:call:fs:read"]
    assert mgr.calls == [("fs", "read", {"path": "app.py"})]
    assert "read" in text


@pytest.mark.asyncio
async def test_gateway_denies_call_via_governance() -> None:
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.sse import sse_client  # noqa: PLC0415

    mgr, gov = _Mgr("strict"), _Gov(allowed=False)
    tools = [GatewayTool("fs__write", "fs", "write", "Write", {"type": "object"})]
    async with mcp_gateway(tools, mgr, gov, agent_id="coder") as gw:
        headers = {"Authorization": f"Bearer {gw.token}"}
        async with (
            sse_client(gw.url, headers=headers) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("fs__write", {"path": "x"})
            text = result.content[0].text  # type: ignore[union-attr]
    assert "DENIED by governance" in text
    assert mgr.calls == []  # never reached the real server


@pytest.mark.asyncio
async def test_gateway_advertises_container_host() -> None:
    # Bind all interfaces but advertise host.docker.internal so a container reaches the host.
    tools = [GatewayTool("fs__read", "fs", "read", "Read", {"type": "object"})]
    async with mcp_gateway(
        tools, _Mgr(), _Gov(), agent_id="c", host="0.0.0.0", advertise_host="host.docker.internal"
    ) as gw:
        assert gw.url.startswith("http://host.docker.internal:")
        assert gw.url.endswith("/sse")


@pytest.mark.asyncio
async def test_gateway_rejects_wrong_token() -> None:
    from mcp.client.sse import sse_client  # noqa: PLC0415

    mgr, gov = _Mgr(), _Gov()
    tools = [GatewayTool("fs__read", "fs", "read", "Read", {"type": "object"})]
    async with mcp_gateway(tools, mgr, gov, agent_id="coder") as gw:
        with pytest.raises(Exception):  # noqa: B017 — 401 surfaces as a connection error
            async with sse_client(gw.url, headers={"Authorization": "Bearer wrong"}) as (r, _w):
                async for _ in r:  # pragma: no cover
                    break
