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


class _Resp:
    """The real `call_tool` returns a `ToolResponse`; the double returned a bare string, which is
    how a governed-call change that reads the response could pass here and fail in a run."""

    def __init__(self, *, is_error: bool = False) -> None:
        self.data = type("D", (), {"text": "OK", "isError": is_error})()
        self.metadata = type("M", (), {"source": "fs", "duration_ms": 1})()


class _Gov:
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed
        self.actions: list[str] = []

    async def evaluate_action(self, *, action: str, **_: object) -> PolicyDecision:
        self.actions.append(action)
        return PolicyDecision(allowed=self._allowed, reason="" if self._allowed else "nope", tier=1)

    async def record_event(self, event: AuditEvent) -> None: ...


class _Mgr:
    def __init__(self, permission: str = "cautious", effects: str = "unknown") -> None:
        self._perm = permission
        self._effects = effects
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def get_permission(self, server_id: str, tool_name: str) -> str:
        return self._perm

    def get_effects(self, server_id: str, tool_name: str) -> str:
        return self._effects

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> _Resp:
        self.calls.append((server_id, tool_name, arguments))
        return _Resp()


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
    assert getattr(resp.data, "text", None) == "OK"
    assert mgr.calls == [("fs", "read", {"x": 1})]


@pytest.mark.asyncio
async def test_governed_call_raises_on_deny_and_never_calls() -> None:
    mgr = _Mgr("strict")
    with pytest.raises(MCPCallDenied, match="nope"):
        await governed_mcp_call(
            mgr, _Gov(allowed=False), agent_id="a", server_id="fs", tool_name="write"
        )
    assert mgr.calls == []  # refused before the server is ever touched


@pytest.mark.asyncio
async def test_an_unmet_prerequisite_denies_before_the_policy_call() -> None:
    """The second reason to deny (skill-prerequisites.md). Checked before `evaluate_action`,
    because an ordering refusal is not a policy question and evaluating a call that is about to be
    refused would put a misleading allow in the record."""
    from swarmkit_runtime import prerequisites  # noqa: PLC0415

    mgr, gov = _Mgr("cautious"), _Gov(allowed=True)

    with pytest.raises(MCPCallDenied, match="list-conventions"):
        await governed_mcp_call(
            mgr,
            gov,
            agent_id="a",
            server_id="fs",
            tool_name="read",
            skill_id="read-file",
            requires={"read-file": ("list-conventions",)},
            run_id="run-governed",
        )

    assert mgr.calls == []
    assert gov.actions == [], "the policy engine was never asked"
    prerequisites.forget_run("run-governed")
