"""`required: false` means advisory, not absent.

`merge_decision_skills` filtered out every binding whose `required` was falsey, so an advisory
binding was accepted, validated, displayed — and never evaluated. There was no path by which one
could run.

`memory-reader` is bound that way by the runtime's own docs and examples, because a memory read that
can fail a run is worse than no memory. So the only sane configuration was the one that silently did
nothing, and the governed-memory read path added in 1.168.0 sat behind it, correct and unreachable.

The two flags mean different things and the code collapsed them:

* `required: true` — a `fail` verdict stops the run.
* `required: false` — the skill runs; a rejection is advisory.

Dropping the second is not a conservative default, it is the opposite of what the setting reads as.

**Removing the filter alone would have been a regression.** Nothing anywhere read `binding.required`
— the flag existed only to decide whether a binding survived the merge — so letting advisory
bindings through without a guard at the verdict would let them FAIL runs, which is worse than the
bug being fixed. Both halves are here.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.governance import DecisionSkillResult, merge_decision_skills
from swarmkit_runtime.langgraph_compiler._decision_gate import _blocking, evaluate_pre_input

ADVISORY = {"id": "memory-reader", "trigger": "pre_input", "required": False, "config": {}}
BLOCKING = {"id": "spec-conformance", "trigger": "post_output", "required": True}


def _result(skill_id: str, verdict: Any = "fail") -> DecisionSkillResult:
    return DecisionSkillResult(
        skill_id=skill_id, verdict=verdict, confidence=1.0, reasoning="because"
    )


class _Governance:
    """Answers every evaluation with the verdict it was constructed with."""

    def __init__(self, verdict: str = "fail") -> None:
        self.verdict = verdict
        self.asked: list[str] = []

    async def evaluate_decision_skill(self, *, skill_id: str, **_kw: Any) -> DecisionSkillResult:
        self.asked.append(skill_id)
        return _result(skill_id, self.verdict)


# ---- the binding survives the merge ------------------------------------------------------------


def test_an_advisory_binding_is_not_discarded() -> None:
    """The reported reproduction: this returned an empty list."""
    merged = merge_decision_skills([ADVISORY], [])

    assert [b.id for b in merged] == ["memory-reader"]
    assert merged[0].required is False


def test_a_required_binding_still_survives() -> None:
    assert [b.id for b in merge_decision_skills([], [BLOCKING])] == ["spec-conformance"]


def test_both_kinds_come_through_together() -> None:
    merged = merge_decision_skills([ADVISORY], [BLOCKING])

    assert {b.id for b in merged} == {"memory-reader", "spec-conformance"}


def test_the_topology_still_overrides_the_workspace() -> None:
    """The merge's actual job, unchanged: same id, topology wins."""
    override = {"id": "memory-reader", "trigger": "pre_input", "required": True}

    merged = merge_decision_skills([ADVISORY], [override])

    assert len(merged) == 1
    assert merged[0].required is True


# ---- and advisory still means non-blocking -----------------------------------------------------


def test_an_advisory_failure_does_not_block() -> None:
    """The half that makes removing the filter safe. Nothing read `required` before, so letting
    these through unguarded would let a memory read abort a run."""
    bindings = merge_decision_skills([ADVISORY], [])

    assert _blocking([_result("memory-reader")], bindings) == []


def test_a_required_failure_does_block() -> None:
    bindings = merge_decision_skills([], [{**BLOCKING, "trigger": "pre_input"}])

    assert len(_blocking([_result("spec-conformance")], bindings)) == 1


def test_a_pass_never_blocks() -> None:
    bindings = merge_decision_skills([], [{**BLOCKING, "trigger": "pre_input"}])

    assert _blocking([_result("spec-conformance", "pass")], bindings) == []


def test_only_the_named_verdicts_block() -> None:
    """`needs-revision` blocks where a caller asks for it, and not otherwise."""
    bindings = merge_decision_skills([], [{**BLOCKING, "trigger": "pre_input"}])
    results = [_result("spec-conformance", "needs-revision")]

    assert _blocking(results, bindings) == []
    assert len(_blocking(results, bindings, ("fail", "needs-revision"))) == 1


# ---- through the real gate ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_advisory_skill_runs_and_the_input_proceeds() -> None:
    """Both properties at once, which is the whole point: it IS evaluated, and its rejection does
    not stop the run."""
    governance = _Governance("fail")
    bindings = merge_decision_skills([ADVISORY], [])

    proceed, message, results = await evaluate_pre_input(
        agent_id="triage",
        user_input="count the cartons",
        bindings=bindings,
        governance=governance,  # type: ignore[arg-type]
    )

    assert governance.asked == ["memory-reader"], "the skill must actually be evaluated"
    assert proceed is True, "an advisory rejection must not stop the run"
    assert message is None
    assert len(results) == 1, "and its result is still reported"


@pytest.mark.asyncio
async def test_a_required_skill_still_stops_the_run() -> None:
    governance = _Governance("fail")
    bindings = merge_decision_skills([], [{**BLOCKING, "trigger": "pre_input"}])

    proceed, message, _ = await evaluate_pre_input(
        agent_id="triage",
        user_input="count the cartons",
        bindings=bindings,
        governance=governance,  # type: ignore[arg-type]
    )

    assert proceed is False
    assert message


# ---- the chain bug 21's fix depends on ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_curated_fact_reaches_a_run_through_the_real_merge() -> None:
    """Bug 21's fix, verified through the path a workspace actually uses.

    Its original test constructed a binding by hand and passed it straight to the gate, which
    bypassed the merge entirely — so it passed while the feature was unreachable, and bug 21 was
    marked fixed on the strength of it. This drives the merge.
    """
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from sqlalchemy import create_engine  # noqa: PLC0415
    from swarmkit_runtime.governed_memory import GovernedMemoryStore  # noqa: PLC0415
    from swarmkit_runtime.governed_memory._models import MemoryCandidate  # noqa: PLC0415
    from swarmkit_runtime.memory._gate import memory_pre_input  # noqa: PLC0415

    store = GovernedMemoryStore(
        create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'memory.sqlite'}")
    )
    store.write(
        MemoryCandidate(
            subject="sn8",
            attribute="carton-count-source",
            value="Carton count comes from the TASK LIST, not YFS_SHIPMENT_CONTAINER.",
        )
    )

    context = await memory_pre_input(
        agent_id="triage",
        user_input="enumerate the cartons for this outbound shipment",
        bindings=merge_decision_skills([ADVISORY], []),
        store=None,
        governed_store=store,
    )

    assert context is not None, "the advisory binding must survive the merge for this to run"
    assert "TASK LIST" in context
