"""Every tool call is audited, not just the first turn's.

Bug 15. `skill.executed` was emitted from a single site reached by the initial model call. The
multi-turn loop executes every subsequent call and emitted nothing, so coverage was not "some calls
are dropped" — it was structural: turn 1 audited, turns 2..n not. An agent that made 16 calls was
recorded as having made 1, and since the first turn is usually a single orienting call, the log kept
the least informative fraction of the run.

This workspace treats the audit log as the record of what an agent touched. Nothing unauthorised was
hidden — every skill was `kb:read` — but the guarantee that the log SHOWS what was read was not
true, and that is the property the log exists to provide.

It also misled diagnosis. While bug 14 was being investigated, an agent's output cited tools the
audit log did not show, which reads as fabricated citations; the calls had simply happened in turns
nothing recorded. A false fabrication finding against a model is an expensive kind of wrong.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.langgraph_compiler._tool_loop import _record_skill_executed


class _Governance:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record_event(self, event: Any) -> None:
        self.events.append(event)


# ---- the event the loop now emits ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_call_is_recorded() -> None:
    gov = _Governance()

    await _record_skill_executed(
        gov,  # type: ignore[arg-type]
        agent_id="triage",
        skill_id="search-wms-tables",
        arguments={"query": "PGM"},
        result="3 tables",
    )

    assert len(gov.events) == 1
    assert gov.events[0].event_type == "skill.executed"
    assert gov.events[0].skill_id == "search-wms-tables"
    assert gov.events[0].agent_id == "triage"


@pytest.mark.asyncio
async def test_the_arguments_and_result_are_kept() -> None:
    """Without them the row says a tool ran but not what it was asked or what came back — which is
    most of why anyone reads the log."""
    gov = _Governance()

    await _record_skill_executed(
        gov,  # type: ignore[arg-type]
        agent_id="triage",
        skill_id="search-sterling-docs",
        arguments={"query": "pick confirm", "limit": 40},
        result="found 12 documents",
    )

    payload = gov.events[0].payload
    assert payload["inputs"] == {"query": "pick confirm", "limit": 40}
    assert "found 12 documents" in payload["outputs"]["result"]


@pytest.mark.asyncio
async def test_a_large_result_is_bounded() -> None:
    """The audit log is not a copy of every tool's output; the same 1000-char bound the initial
    turn already applies."""
    gov = _Governance()

    await _record_skill_executed(
        gov,  # type: ignore[arg-type]
        agent_id="a",
        skill_id="s",
        arguments={},
        result="x" * 5000,
    )

    assert len(gov.events[0].payload["outputs"]["result"]) == 1000


@pytest.mark.asyncio
async def test_the_policy_decision_is_stated() -> None:
    """Every `skill.executed` row had a NULL policy_decision, so a reader could not tell "allowed"
    from "never evaluated" — two very different things in a governance record."""
    gov = _Governance()

    await _record_skill_executed(
        gov,  # type: ignore[arg-type]
        agent_id="a",
        skill_id="s",
        arguments={},
        result="r",
    )

    assert gov.events[0].policy_decision == "allow"


@pytest.mark.asyncio
async def test_the_duration_is_recorded_as_an_int() -> None:
    gov = _Governance()

    await _record_skill_executed(
        gov,  # type: ignore[arg-type]
        agent_id="a",
        skill_id="s",
        arguments={},
        result="r",
        duration_ms=12.7,
    )

    assert gov.events[0].duration_ms == 12


# ---- the shape matches the initial turn -----------------------------------------------------


def test_the_event_shape_matches_the_initial_turn() -> None:
    """Same event_type and fields as the site that already emitted for turn 1, so every existing
    reader works unchanged. The point is coverage, not a new format."""
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/langgraph_compiler"
    delegation = (root / "_delegation.py").read_text()
    loop = (root / "_tool_loop.py").read_text()

    assert 'event_type="skill.executed"' in delegation
    assert 'event_type="skill.executed"' in loop
    for field in ("skill_id=", "agent_id=", '"inputs"', '"outputs"'):
        assert field in loop, f"the loop's event is missing {field}"


def test_the_loop_no_longer_emits_nothing() -> None:
    """The bug as a count. It was zero: `grep -c AuditEvent _tool_loop.py` returned 0 while the
    same grep on the initial-turn path returned 3."""
    from pathlib import Path  # noqa: PLC0415

    loop = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_tool_loop.py"
    ).read_text()

    assert loop.count("AuditEvent(") >= 1, "the multi-turn loop must record what it executes"


def test_every_dispatched_call_is_audited_not_only_the_last() -> None:
    """The emission sits inside the per-call loop, next to where the result is appended — not after
    it. Placed outside, only the final call of each turn would be recorded, which is the same class
    of bug one level down."""
    from pathlib import Path  # noqa: PLC0415

    loop = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_tool_loop.py"
    ).read_text()

    # The LAST append is the skill-dispatch one; the earlier three are built-in coordination
    # tools that return early (see the test below).
    body = loop.split("results.append(")[-1].split("return results if results else None")[0]
    assert "_record_skill_executed(" in body, (
        "the audit call must be inside the per-call loop, not after it"
    )


def test_built_in_coordination_tools_are_deliberately_not_audited_as_skills() -> None:
    """`create-scope`, `read-task-result` and `context_retrieve` are runtime built-ins, not
    workspace skills — they have no skill id, no IAM scopes and no provenance. Recording them as
    `skill.executed` would put things in the governance record that are not skills, which is a
    different kind of wrong from omitting them.

    They are genuinely unaudited today. That is a separate question from bug 15 and is noted rather
    than quietly folded in.
    """
    from pathlib import Path  # noqa: PLC0415

    loop = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_tool_loop.py"
    ).read_text()

    before_skill_dispatch = loop.split("results.append(")[0]
    assert "_record_skill_executed(" not in before_skill_dispatch
