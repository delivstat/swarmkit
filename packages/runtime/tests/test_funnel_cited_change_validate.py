"""cited_change wired as a funnel validate layer + the diff threaded to the gate
(design/details/funnel-deterministic-validate.md, step 2).

The gated node's artifact is a change-rationale (`summary` + `citations`); the produced diff is
threaded node -> produce -> gate state -> validator via ValidateContext. An uncited change is a
validate failure that drives the bounded retry and escalates to the human approve layer (never
drops). slice_budget and cited_change compose — both run, both must pass. Layer behaviours are
injected as fakes, per test_gate_funnel.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.langgraph_compiler._gate_funnel import (
    ApproveOutcome,
    ValidateContext,
    build_deterministic_validator,
    compile_funnel_gate,
)

pytestmark = pytest.mark.asyncio

# A one-file diff adding lines 2 and 3 of reserve.py.
_DIFF = "--- a/reserve.py\n+++ b/reserve.py\n@@ -1,1 +1,3 @@\n a\n+b\n+c\n"
# A rationale that cites a changed line in the touched file — resolves.
_CITED = (
    "summary: reserve stock\ncitations:\n  - claim: adds b\n    path: reserve.py\n    lines: [2]\n"
)
# A rationale citing a line the diff did not change — uncited.
_UNCITED = (
    "summary: reserve stock\ncitations:\n  - claim: adds x\n    path: reserve.py\n    lines: [99]\n"
)


async def _approve(state: dict[str, Any]) -> ApproveOutcome:
    return ApproveOutcome(approved=True, detail="approved")


def _spec(**layers: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "approve": {"rules": [{"scope": "x:y", "roles": ["a"], "quorum": "all"}]}
    }
    base.update(layers)
    return base


async def test_validator_built_for_cited_change_and_resolves_against_diff() -> None:
    assert build_deterministic_validator(_spec()) is None
    validator = build_deterministic_validator(_spec(validate={"cited_change": True}))
    assert validator is not None

    ok = await validator(ValidateContext(artifact=_CITED, diff=_DIFF))
    assert ok.ok is True

    bad = await validator(ValidateContext(artifact=_UNCITED, diff=_DIFF))
    assert bad.ok is False
    assert "uncited change" in bad.detail  # the retry critique

    # No diff threaded ⇒ nothing resolves ⇒ fail-closed (escalates to the human).
    assert (await validator(ValidateContext(artifact=_CITED, diff=None))).ok is False


async def test_slice_budget_and_cited_change_compose() -> None:
    # Both configured: an over-budget-but-cited change still fails on budget.
    validator = build_deterministic_validator(
        _spec(validate={"slice_budget": {"max_diff_lines": 1}, "cited_change": True})
    )
    assert validator is not None
    out = await validator(ValidateContext(artifact=_CITED, diff=_DIFF))  # 2 added lines > 1
    assert out.ok is False
    assert "over slice budget" in out.detail  # budget checked first


async def test_threaded_diff_reaches_validator_via_diff_source() -> None:
    # draft_node refreshes `diff` from diff_source on every (re)draft; validate sees it.
    spec = _spec(validate={"cited_change": True})

    async def drafter(state: dict[str, Any]) -> str:
        return _CITED

    compiled = compile_funnel_gate(
        spec,
        drafter=drafter,
        approver=_approve,
        validator=build_deterministic_validator(spec),
        diff_source=lambda: _DIFF,
    )
    result = await compiled.ainvoke({"artifact": _CITED, "retries": 0})
    assert result["outcome"] == "approved"
    assert result["provenance"]["escalated"] is False


async def test_uncited_change_escalates_to_human_never_drops() -> None:
    spec = _spec(validate={"cited_change": True})

    async def drafter(state: dict[str, Any]) -> str:
        return _UNCITED  # every revision stays uncited → retries exhaust

    compiled = compile_funnel_gate(
        spec,
        drafter=drafter,
        approver=_approve,
        validator=build_deterministic_validator(spec),
        diff_source=lambda: _DIFF,
    )
    result = await compiled.ainvoke({"artifact": _UNCITED, "retries": 0})
    assert result["outcome"] == "approved"  # the only exit is the human approve
    assert result["provenance"]["escalated"] is True
