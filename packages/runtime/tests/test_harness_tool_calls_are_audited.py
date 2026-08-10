"""A harness run records WHICH tools it called, not just how many.

A harness run recorded one event at the end — `executor.mcp_usage: advertised 43, calls 55` — and
nothing about which tools, with what, or to what effect. Bug 15 fixed per-tool audit for the model
tool loop; the harness path kept a counter.

For a research task the tool list *is* the deliverable, and a bare count cannot separate the two
cases that matter most: **"called it and ignored the answer"** and **"never called it"** are the
same number. That ambiguity produced a wrong diagnosis.

The gateway already had everything needed at the call site — `server_id`, `tool_name`, the
arguments and the response — and threw it away. The event emitted here is the same `skill.executed`
in the same shape the model loop emits (`_tool_loop._record_tool_call`), so every existing reader
works unchanged: the point is coverage, not a new format.

**The run-scope interaction, which is why this is not a one-liner.** Tool calls are served on
uvicorn's tasks, and those do not inherit the run's `ContextVar` scope — so an event emitted at call
time carries no `run_id`, and the run-scoped drain added in 1.176.0 discards it. The record would
exist and reach nothing, which is this codebase's signature failure. The registration is created
inside the run's task, so the scope is captured there and stamped explicitly.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from swarmkit_runtime._run_scope import (
    reset_current_labels,
    reset_current_run_id,
    set_current_labels,
    set_current_run_id,
)
from swarmkit_runtime.governance import AuditEvent, PolicyDecision
from swarmkit_runtime.mcp._gateway import GatewayTool, mcp_gateway

pytestmark = pytest.mark.asyncio


class _Resp:
    def __init__(self, text: str) -> None:
        self.data = type("D", (), {"content": [type("B", (), {"text": text})()]})()


class _Mgr:
    def __init__(self, permission: str = "cautious") -> None:
        self._perm = permission

    def get_permission(self, server_id: str, tool_name: str) -> str:
        return self._perm

    def get_tool_input_schema(self, server_id: str, tool_name: str) -> dict[str, Any]:
        return {}

    async def call_tool(
        self, server_id: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> _Resp:
        return _Resp(f"contents of {(arguments or {}).get('path')}")


class _Gov:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed
        self.events: list[AuditEvent] = []

    async def evaluate_action(self, *, action: str, **_: object) -> PolicyDecision:
        return PolicyDecision(allowed=self._allowed, reason="" if self._allowed else "nope", tier=1)

    async def record_event(self, event: AuditEvent) -> None:
        self.events.append(event)


TOOLS = [
    GatewayTool("fs__read", "fs", "read", "Read a file", {"type": "object"}),
    GatewayTool("fs__write", "fs", "write", "Write a file", {"type": "object"}),
]


async def _call(gov: _Gov, mgr: _Mgr, name: str, args: dict[str, Any]) -> str:
    """Drive one tool call through a live gateway, the way a harness does."""
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.sse import sse_client  # noqa: PLC0415

    async with mcp_gateway(
        TOOLS,
        cast("Any", mgr),
        cast("Any", gov),
        agent_id="researcher",
    ) as gw:
        headers = {"Authorization": f"Bearer {gw.token}"}
        async with (
            sse_client(gw.url, headers=headers) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, args)
            return str(result.content[0].text)  # type: ignore[union-attr]


def _executions(gov: _Gov) -> list[AuditEvent]:
    return [e for e in gov.events if e.event_type == "skill.executed"]


# ---- which tool, with what, to what effect -----------------------------------------------------


async def test_a_harness_tool_call_is_audited_by_name() -> None:
    """The report: 55 calls and no way to know what any of them were."""
    gov, mgr = _Gov(), _Mgr()

    await _call(gov, mgr, "fs__read", {"path": "app.py"})

    assert [e.skill_id for e in _executions(gov)] == ["fs__read"]


async def test_the_arguments_are_recorded() -> None:
    """ "Which file did it read" is the question a research ticket is answering."""
    gov, mgr = _Gov(), _Mgr()

    await _call(gov, mgr, "fs__read", {"path": "app.py"})

    assert _executions(gov)[0].payload["inputs"] == {"path": "app.py"}


async def test_the_result_is_recorded() -> None:
    """The half that separates "called it and ignored the answer" from "never called it"."""
    gov, mgr = _Gov(), _Mgr()

    await _call(gov, mgr, "fs__read", {"path": "app.py"})

    outputs = cast("dict[str, Any]", _executions(gov)[0].payload["outputs"])
    assert "contents of app.py" in str(outputs["result"])


async def test_an_allowed_call_states_its_policy_decision() -> None:
    """Stated rather than left null: a reader could not otherwise tell "allowed" from "never
    evaluated", which is the same reasoning the model loop's record uses."""
    gov, mgr = _Gov(), _Mgr()

    await _call(gov, mgr, "fs__read", {"path": "app.py"})

    assert _executions(gov)[0].policy_decision == "allow"


async def test_the_call_is_timed() -> None:
    gov, mgr = _Gov(), _Mgr()

    await _call(gov, mgr, "fs__read", {"path": "app.py"})

    assert _executions(gov)[0].duration_ms is not None


# ---- the calls that used to be invisible --------------------------------------------------------


async def test_a_denied_call_is_audited_as_denied() -> None:
    """A refusal reached the model as ordinary text and was recorded nowhere."""
    gov, mgr = _Gov(allowed=False), _Mgr("strict")

    text = await _call(gov, mgr, "fs__write", {"path": "app.py"})

    assert "DENIED" in text
    assert [(e.skill_id, e.policy_decision) for e in _executions(gov)] == [("fs__write", "deny")]


async def test_a_call_to_an_ungranted_tool_is_audited() -> None:
    """An agent reaching for a tool it was not granted is a governance signal, not a typo."""
    gov, mgr = _Gov(), _Mgr()

    await _call(gov, mgr, "fs__delete", {"path": "app.py"})

    assert [(e.skill_id, e.policy_decision) for e in _executions(gov)] == [("fs__delete", "deny")]


# ---- the record has to reach the run ------------------------------------------------------------


async def test_the_event_carries_the_run_it_belongs_to() -> None:
    """The interaction that makes this more than a one-liner.

    The call is served on uvicorn's task, which does not inherit the run's ContextVar scope. An
    event stamped there would carry no `run_id`, and the run-scoped drain would discard it — the
    record would exist and reach nothing. The scope is captured at REGISTRATION, inside the run.
    """
    gov, mgr = _Gov(), _Mgr()
    run_token = set_current_run_id("WMS-30:research")
    label_token = set_current_labels({"map": "WMS-30"})
    try:
        await _call(gov, mgr, "fs__read", {"path": "app.py"})
    finally:
        reset_current_labels(label_token)
        reset_current_run_id(run_token)

    event = _executions(gov)[0]
    assert event.run_id == "WMS-30:research"
    assert event.labels == {"map": "WMS-30"}


async def test_the_event_survives_the_run_scoped_drain() -> None:
    """Stated end-to-end, because "stamped correctly" and "actually persisted" came apart once."""
    from swarmkit_runtime._workspace_runtime import _extract_events  # noqa: PLC0415

    gov, mgr = _Gov(), _Mgr()
    run_token = set_current_run_id("WMS-30:research")
    try:
        await _call(gov, mgr, "fs__read", {"path": "app.py"})
    finally:
        reset_current_run_id(run_token)

    drained = _extract_events(cast("Any", gov), run_id="WMS-30:research")

    assert "fs__read" in {e.skill_id for e in drained}


# ---- and it never breaks the call ---------------------------------------------------------------


async def test_a_failing_audit_does_not_fail_the_tool_call() -> None:
    """The gateway's job is to serve the call. An audit backend that is down must not take tools
    away from a running agent."""

    class _Broken(_Gov):
        async def record_event(self, event: AuditEvent) -> None:
            raise RuntimeError("audit backend down")

    text = await _call(_Broken(), _Mgr(), "fs__read", {"path": "app.py"})

    assert "contents of app.py" in text
