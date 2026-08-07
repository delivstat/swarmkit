"""A bound decision skill actually executes.

`Trigger` was generated as a plain `Enum`, so `Trigger.pre_input == "pre_input"` was **False** — and
every selection site in the runtime compares the binding's trigger against a string literal. All
four trigger points therefore selected nothing, for every binding, always. No decision skill of any
kind had ever fired.

Nothing errored. An empty selection is indistinguishable from "none configured", so the workspace
believed `spec-conformance` was gating every design spec while it had never run once. A
`required: true` binding silently not running is the worst case here.

This took three bug reports to reach, because each layer had a real defect in front of it:

* bug 21 — the reader could not see governed memory (fixed 1.168.0, no observable change);
* bug 22 — `merge_decision_skills` discarded advisory bindings (fixed 1.169.0, also invisible);
* this — the binding now arrives and is still never selected.

Every one of those fixes was correct and unverifiable, because each was tested at its own seam with
hand-built inputs. **These tests assert that a skill RAN**, from a binding constructed the way a
`workspace.yaml` produces one — which is the only assertion that would have caught any of the three.

The scope defect is here too, and deliberately so: fixing the enum alone converts a silent no-op
into an `AttributeError` on every unscoped workspace binding, which is the documented way to bind
`memory-reader`. They had to land together.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.governance import DecisionSkillResult, merge_decision_skills
from swarmkit_runtime.langgraph_compiler._decision_gate import evaluate_pre_input
from swarmkit_schema.models.topology import Trigger as TopologyTrigger
from swarmkit_schema.models.workspace import DecisionSkillBinding as WorkspaceBinding
from swarmkit_schema.models.workspace import Trigger as WorkspaceTrigger


def _declared(**over: Any) -> dict[str, Any]:
    """A binding as `workspace.yaml` produces one: through pydantic, then `model_dump()`.

    That round trip is what made this invisible. Hand-built dicts have string triggers and absent
    optional keys; a real one has enum members and keys present-and-None.
    """
    fields: dict[str, Any] = {"id": "memory-reader", "trigger": "pre_input", "required": False}
    fields.update(over)
    return WorkspaceBinding(**fields).model_dump()


class _Governance:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def evaluate_decision_skill(self, *, skill_id: str, **_kw: Any) -> DecisionSkillResult:
        self.asked.append(skill_id)
        return DecisionSkillResult(
            skill_id=skill_id, verdict="pass", confidence=1.0, reasoning="ok"
        )


# ---- the enum compares equal to its own string ------------------------------------------------


@pytest.mark.parametrize("trigger", ["pre_input", "post_output", "checkpoint", "pre_synthesis"])
def test_a_trigger_equals_its_string(trigger: str) -> None:
    """The reported reproduction. Every one of these was False."""
    assert WorkspaceTrigger(trigger) == trigger
    assert TopologyTrigger(trigger) == trigger


def test_both_generated_triggers_are_str_enums() -> None:
    """Stated against the type, because the schema is what drives it: an enum without a declared
    `type: string` generates a plain Enum, and the next comparison written against one has this bug
    again."""
    assert issubclass(WorkspaceTrigger, str)
    assert issubclass(TopologyTrigger, str)


# ---- a declared binding survives the round trip -----------------------------------------------


def test_a_declared_binding_has_a_comparable_trigger() -> None:
    binding = merge_decision_skills([_declared()], [])[0]

    assert binding.trigger == "pre_input"


def test_an_unscoped_binding_applies_to_every_agent() -> None:
    """`model_dump()` emits an unset field as None, and a dict default only fires when the KEY is
    missing — so an unscoped binding arrived as `scope=None` and raised. Masked until now by the
    trigger comparison short-circuiting first."""
    binding = merge_decision_skills([_declared()], [])[0]

    assert binding.applies_to("triage") is True
    assert binding.applies_to("anything-else") is True


def test_a_scoped_binding_still_scopes() -> None:
    binding = merge_decision_skills([_declared(scope="triage,design")], [])[0]

    assert binding.applies_to("triage") is True
    assert binding.applies_to("publisher") is False


def test_an_unset_config_is_a_dict_not_none() -> None:
    """Same None-vs-absent trap, one field over."""
    assert merge_decision_skills([_declared()], [])[0].config == {}


def test_required_survives_the_round_trip() -> None:
    assert merge_decision_skills([_declared(required=False)], [])[0].required is False
    assert merge_decision_skills([_declared(required=True)], [])[0].required is True


# ---- and the skill RUNS -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bound_skill_is_actually_evaluated() -> None:
    """The assertion missing from every fix in this chain. Each was correct at its own seam and
    unreachable in the path a workspace uses; only "did it run" catches that."""
    governance = _Governance()
    bindings = merge_decision_skills([_declared(id="spec-conformance", required=True)], [])

    await evaluate_pre_input(
        agent_id="triage",
        user_input="anything",
        bindings=bindings,
        governance=governance,  # type: ignore[arg-type]
    )

    assert governance.asked == ["spec-conformance"], "the binding must reach the evaluator"


@pytest.mark.asyncio
async def test_a_binding_scoped_to_another_agent_does_not_fire() -> None:
    """The selection still selects — this is not "run everything"."""
    governance = _Governance()
    bindings = merge_decision_skills([_declared(scope="design")], [])

    await evaluate_pre_input(
        agent_id="triage",
        user_input="anything",
        bindings=bindings,
        governance=governance,  # type: ignore[arg-type]
    )

    assert governance.asked == []


@pytest.mark.asyncio
async def test_a_binding_for_another_trigger_does_not_fire() -> None:
    governance = _Governance()
    bindings = merge_decision_skills([_declared(trigger="post_output")], [])

    await evaluate_pre_input(
        agent_id="triage",
        user_input="anything",
        bindings=bindings,
        governance=governance,  # type: ignore[arg-type]
    )

    assert governance.asked == []


# ---- the whole chain, from a declared binding to an injected fact -----------------------------


@pytest.mark.asyncio
async def test_a_curated_fact_reaches_the_agent_from_a_declared_binding() -> None:
    """Bugs 21, 22 and 23 in one assertion: declared in pydantic, merged, selected by trigger,
    scoped to the agent, read from governed memory, rendered into the input."""
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
        bindings=merge_decision_skills([_declared(config={"governed_limit": 5})], []),
        store=None,
        governed_store=store,
    )

    assert context is not None, "the binding must be selected for its trigger to get this far"
    assert "TASK LIST" in context
