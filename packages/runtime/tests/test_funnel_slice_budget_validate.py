"""slice_budget wired as a funnel validate layer (design/details/funnel-deterministic-validate.md).

The deterministic validator is built only when `validate.slice_budget` is set; an over-budget diff
is a validate failure that drives the funnel's bounded retry and escalates to the human approve
layer (never drops). Layer behaviours are injected as fakes, per test_gate_funnel.py.
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

_OVER = "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,4 @@\n a\n+b\n+c\n+d\n"  # 3 added lines, 1 file
_UNDER = "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,2 @@\n a\n+b\n"  # 1 added line, 1 file


async def _approve(state: dict[str, Any]) -> ApproveOutcome:
    return ApproveOutcome(approved=True, detail="approved")


def _spec(**layers: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "approve": {"rules": [{"scope": "x:y", "roles": ["a"], "quorum": "all"}]}
    }
    base.update(layers)
    return base


async def test_validator_built_only_for_slice_budget_and_enforces() -> None:
    # built only when a deterministic check is configured (schema-only stays output-governance)
    assert build_deterministic_validator(_spec()) is None
    assert build_deterministic_validator(_spec(validate={"schema": "s.json"})) is None

    validator = build_deterministic_validator(
        _spec(validate={"slice_budget": {"max_diff_lines": 2}})
    )
    assert validator is not None
    # No threaded diff ⇒ the artifact itself is treated as the diff (fallback).
    assert (await validator(ValidateContext(artifact=_UNDER))).ok is True
    over = await validator(ValidateContext(artifact=_OVER))
    assert over.ok is False
    assert "over slice budget" in over.detail  # the retry critique
    # A threaded diff takes precedence over the artifact.
    assert (await validator(ValidateContext(artifact="rationale", diff=_UNDER))).ok is True
    assert (await validator(ValidateContext(artifact="rationale", diff=_OVER))).ok is False


async def test_within_budget_reaches_approval() -> None:
    spec = _spec(validate={"slice_budget": {"max_diff_lines": 5}})

    async def drafter(state: dict[str, Any]) -> str:
        return _UNDER

    compiled = compile_funnel_gate(
        spec, drafter=drafter, approver=_approve, validator=build_deterministic_validator(spec)
    )
    result = await compiled.ainvoke({"artifact": _UNDER, "retries": 0})
    assert result["outcome"] == "approved"
    assert result["provenance"]["escalated"] is False


async def test_over_budget_escalates_to_human_never_drops() -> None:
    spec = _spec(validate={"slice_budget": {"max_diff_lines": 2}})

    async def drafter(state: dict[str, Any]) -> str:
        return _OVER  # every revision is still over budget → retries exhaust

    compiled = compile_funnel_gate(
        spec, drafter=drafter, approver=_approve, validator=build_deterministic_validator(spec)
    )
    result = await compiled.ainvoke({"artifact": _OVER, "retries": 0})
    assert result["outcome"] == "approved"  # the only exit is the human approve
    assert result["provenance"]["escalated"] is True
