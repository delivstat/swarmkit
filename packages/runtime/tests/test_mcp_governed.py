"""The one governed MCP-call path (executor-mcp-gateway.md, task #22).

Every MCP call — model-agent skill or harness-via-gateway — passes the same gate: tier resolution +
governance.evaluate_action + audit. These cover that gate in isolation with structural doubles.
"""

# The doubles duck-type MCPClientManager / GovernanceProvider without inheriting them.
# mypy: disable-error-code="arg-type, comparison-overlap"

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.governance import AuditEvent, PolicyDecision
from swarmkit_runtime.mcp import MCPCallDenied, check_mcp_permission, governed_mcp_call


class _Gov:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed
        self.actions: list[str] = []

    async def evaluate_action(self, *, action: str, **_: object) -> PolicyDecision:
        self.actions.append(action)
        return PolicyDecision(allowed=self._allowed, reason="" if self._allowed else "nope", tier=1)

    async def record_event(self, event: AuditEvent) -> None: ...


class _Mgr:
    def __init__(self, permission: str = "cautious") -> None:
        self._perm = permission
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def get_permission(self, server_id: str, tool_name: str) -> str:
        return self._perm

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> str:
        self.calls.append((server_id, tool_name, arguments))
        return "OK"


# --- check_mcp_permission -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_tool_skips_governance() -> None:
    gov = _Gov(allowed=False)  # would deny — but open must not consult it
    allowed, _reason = await check_mcp_permission(
        _Mgr("open"), gov, agent_id="a", server_id="fs", tool_name="read"
    )
    assert allowed is True
    assert gov.actions == []  # open ⇒ no evaluate_action


@pytest.mark.asyncio
async def test_cautious_tool_is_evaluated_with_the_action_string() -> None:
    gov = _Gov(allowed=True)
    allowed, _ = await check_mcp_permission(
        _Mgr("cautious"), gov, agent_id="a", server_id="fs", tool_name="read"
    )
    assert allowed is True
    assert gov.actions == ["mcp:call:fs:read"]  # the design's action convention


@pytest.mark.asyncio
async def test_denied_tier_refuses_with_reason() -> None:
    allowed, reason = await check_mcp_permission(
        _Mgr("strict"), _Gov(allowed=False), agent_id="a", server_id="fs", tool_name="write"
    )
    assert allowed is False
    assert reason == "nope"


@pytest.mark.asyncio
async def test_no_governance_allows() -> None:
    allowed, _ = await check_mcp_permission(
        _Mgr("strict"), None, agent_id="a", server_id="fs", tool_name="read"
    )
    assert allowed is True  # no provider ⇒ no gate (unchanged behaviour)


# --- governed_mcp_call --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governed_call_invokes_tool_on_allow() -> None:
    mgr = _Mgr("cautious")
    resp = await governed_mcp_call(
        mgr, _Gov(allowed=True), agent_id="a", server_id="fs", tool_name="read", arguments={"x": 1}
    )
    assert resp == "OK"
    assert mgr.calls == [("fs", "read", {"x": 1})]


@pytest.mark.asyncio
async def test_governed_call_raises_on_deny_and_never_calls() -> None:
    mgr = _Mgr("strict")
    with pytest.raises(MCPCallDenied, match="nope"):
        await governed_mcp_call(
            mgr, _Gov(allowed=False), agent_id="a", server_id="fs", tool_name="write"
        )
    assert mgr.calls == []  # refused before the server is ever touched
