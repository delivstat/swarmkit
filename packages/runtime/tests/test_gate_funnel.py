"""Gate funnel subgraph — structural invariant + control-flow behaviour.

The load-bearing tests assert on the *compiled graph shape* (no automated layer
can reach the terminal; only the human ``approve`` node can) and on the bounded
retry / escalation behaviour. Layer behaviours are injected as fakes.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.graph import END
from swarmkit_runtime.langgraph_compiler._gate_funnel import (
    ApproveOutcome,
    JudgeOutcome,
    ReviewOutcome,
    ValidateOutcome,
    compile_funnel_gate,
)

pytestmark = pytest.mark.asyncio


def _end_predecessors(compiled: Any) -> set[str]:
    return {e.source for e in compiled.get_graph().edges if e.target == END}


def _edges(compiled: Any) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in compiled.get_graph().edges}


async def _ok_validator(artifact: str) -> ValidateOutcome:
    return ValidateOutcome(ok=True, artifact=artifact)


async def _pass_judge(artifact: str) -> JudgeOutcome:
    return JudgeOutcome(passed=True, score=0.95, critique="")


async def _approve(state: dict[str, Any]) -> ApproveOutcome:
    return ApproveOutcome(approved=True, detail="approved")


def _spec(**layers: Any) -> dict[str, Any]:
    rules = [{"scope": "x:y", "roles": ["a"], "quorum": "all"}]
    base: dict[str, Any] = {"approve": {"rules": rules}}
    base.update(layers)
    return base


# --- the structural invariant ------------------------------------------------


async def test_only_approve_reaches_end() -> None:
    """No automated layer (validate/judge/review) nor revise has an edge to END."""

    async def drafter(state: dict[str, Any]) -> str:
        return "draft"

    async def reviewer(artifact: str) -> ReviewOutcome:
        return ReviewOutcome(route_back=False)

    compiled = compile_funnel_gate(
        _spec(validate={}, judge={"max_retries": 2}, review={}),
        drafter=drafter,
        approver=_approve,
        validator=_ok_validator,
        judge=_pass_judge,
        reviewer=reviewer,
    )
    assert _end_predecessors(compiled) == {"approve"}
    # No advisory/revise → END edge exists.
    for src in ("validate", "judge", "review", "revise", "draft"):
        assert (src, END) not in _edges(compiled)


async def test_retry_exhaustion_routes_to_human_not_end() -> None:
    """revise (retry) points at draft and approve — never at END."""

    async def drafter(state: dict[str, Any]) -> str:
        return "draft"

    compiled = compile_funnel_gate(
        _spec(judge={"max_retries": 1}),
        drafter=drafter,
        approver=_approve,
        judge=_pass_judge,
    )
    edges = _edges(compiled)
    assert ("revise", "approve") in edges
    assert ("revise", "draft") in edges
    assert ("revise", END) not in edges


async def test_degenerate_funnel_has_no_revise_node() -> None:
    """A funnel with only approve degrades to draft -> approve -> END."""

    async def drafter(state: dict[str, Any]) -> str:
        return "draft"

    compiled = compile_funnel_gate(_spec(), drafter=drafter, approver=_approve)
    nodes = set(compiled.get_graph().nodes.keys())
    assert "revise" not in nodes
    assert _end_predecessors(compiled) == {"approve"}


# --- control-flow behaviour --------------------------------------------------


async def test_happy_path_reaches_approval_with_provenance() -> None:
    async def drafter(state: dict[str, Any]) -> str:
        return "the-artifact"

    compiled = compile_funnel_gate(
        _spec(validate={}, judge={}),
        drafter=drafter,
        approver=_approve,
        validator=_ok_validator,
        judge=_pass_judge,
    )
    result = await compiled.ainvoke({"artifact": "", "retries": 0})
    assert result["outcome"] == "approved"
    assert result["retries"] == 0
    prov = result["provenance"]
    assert prov["judge"]["score"] == 0.95
    assert prov["escalated"] is False


async def test_below_threshold_retries_then_passes() -> None:
    """A judge that fails once then passes drives one revision, then reaches the human."""
    calls = {"n": 0}

    async def drafter(state: dict[str, Any]) -> str:
        return f"draft-{state.get('retries', 0)}"

    async def flaky_judge(artifact: str) -> JudgeOutcome:
        calls["n"] += 1
        if calls["n"] == 1:
            return JudgeOutcome(passed=False, score=0.4, critique="add more detail")
        return JudgeOutcome(passed=True, score=0.9, critique="")

    compiled = compile_funnel_gate(
        _spec(judge={"max_retries": 2}),
        drafter=drafter,
        approver=_approve,
        judge=flaky_judge,
    )
    result = await compiled.ainvoke({"artifact": "", "retries": 0})
    assert result["outcome"] == "approved"
    assert result["retries"] == 1
    assert result["judge"]["passed"] is True


async def test_exhaustion_escalates_to_human() -> None:
    """A judge that always fails escalates to the human after max_retries — never drops."""

    async def drafter(state: dict[str, Any]) -> str:
        return "draft"

    async def failing_judge(artifact: str) -> JudgeOutcome:
        return JudgeOutcome(passed=False, score=0.1, critique="still bad")

    seen: dict[str, Any] = {}

    async def recording_approver(state: dict[str, Any]) -> ApproveOutcome:
        seen.update(state["provenance"])
        return ApproveOutcome(approved=False, detail="human rejected after escalation")

    compiled = compile_funnel_gate(
        _spec(judge={"max_retries": 2}),
        drafter=drafter,
        approver=recording_approver,
        judge=failing_judge,
    )
    result = await compiled.ainvoke({"artifact": "", "retries": 0})
    # Reached the human (escalation), did not silently pass or drop.
    assert result["outcome"] == "rejected"
    assert result["provenance"]["escalated"] is True
    assert result["provenance"]["retries"] == 3  # 2 retries + the exhausting one
    assert seen["critique"] == "still bad"


async def test_review_route_back_then_attach() -> None:
    """A route-back-severity finding retries; a clean review proceeds to the human."""
    calls = {"n": 0}

    async def drafter(state: dict[str, Any]) -> str:
        return "draft"

    async def flaky_reviewer(artifact: str) -> ReviewOutcome:
        calls["n"] += 1
        if calls["n"] == 1:
            return ReviewOutcome(route_back=True, detail="high-severity finding")
        return ReviewOutcome(route_back=False, findings=[{"severity": "low"}])

    compiled = compile_funnel_gate(
        _spec(judge={}, review={"max_retries": 2}),
        drafter=drafter,
        approver=_approve,
        judge=_pass_judge,
        reviewer=flaky_reviewer,
    )
    result = await compiled.ainvoke({"artifact": "", "retries": 0})
    assert result["outcome"] == "approved"
    assert result["retries"] == 1
    assert result["review"]["route_back"] is False
