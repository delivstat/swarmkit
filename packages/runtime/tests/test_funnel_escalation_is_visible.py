"""An escalated artifact says it was escalated, where the reviewer is looking.

A funnel's `validate` layer rejected an artifact, retried the drafter to its limit, and escalated to
the human gate. The escalation was recorded in the audit log and **nowhere the reviewer looks** —
not on the artifact, not on the ticket, not on the gate task. The saga parked exactly as a clean run
does, so a reviewer opened a spec the system already knew violated its own schema with nothing to
say so.

Two gaps, both fixed here:

* **The artifact was not annotated.** The funnel schema says `max_retries` is "retries before the
  funnel escalates to a human with the last critique attached — it never drops or silently passes".
  The escalation happened; the critique was attached to nothing a person reads. The comparable path,
  `_enforce_harness_output_schema`, already appends its failure to the artifact — two mechanisms
  that both escalate on exhaustion behaving differently at the point a person is looking is the
  defect, not the missing string.
* **The payload said which layer, never what was wrong.** `failed_layers: ["validate"]` and nothing
  else, while the field-level errors existed at that moment and were discarded. A reviewer who
  queried `audit_events` by `run_id` still had to re-run the validator by hand to learn why.

Annotating the artifact carries onward for free: the stage's artifact IS this text, so the gate's
review item and the next stage's input both inherit it.

This is the shape after bugs 21/22/23/25 — those were configuration that resolved to nothing; this
is configuration that resolves, works, and reports only where nobody reads.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.langgraph_compiler._compiler import _annotate_escalation
from swarmkit_runtime.langgraph_compiler._gate_funnel import build_advisory_approver

CRITIQUE = (
    "code_changes.10.action: 'modify' is not one of ['new', 'modified', 'deleted']; "
    "impact: 'affected' is a required property"
)


def _out(text: str = '{"summary": "ok"}') -> dict[str, Any]:
    return {"agent_results": {"designer": text}}


def _state(**provenance: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"validate_ok": True, "judge": {}, "review": {}, "retries": 0}
    base.update(provenance)
    return {"outcome": "approved", "provenance": base}


def _text(out: dict[str, Any]) -> str:
    return str(out["agent_results"]["designer"])


# ---- the artifact says so ----------------------------------------------------------------------


def test_a_validate_failure_is_written_onto_the_artifact() -> None:
    """The reported failure: the reviewer opened a spec that already failed its own schema."""
    annotated = _annotate_escalation(
        _out(), "designer", _state(validate_ok=False, retries=2, critique=CRITIQUE)
    )

    assert "FUNNEL GATE ESCALATED" in _text(annotated)
    assert "validate failed after 2 retries" in _text(annotated)


def test_the_critique_is_attached_not_just_the_layer_name() -> None:
    """The schema's own promise: escalation carries the last critique."""
    annotated = _annotate_escalation(
        _out(), "designer", _state(validate_ok=False, retries=2, critique=CRITIQUE)
    )

    assert "'modify' is not one of" in _text(annotated)
    assert "'affected' is a required property" in _text(annotated)


def test_the_original_artifact_is_preserved() -> None:
    """Annotated, not replaced — the reviewer still needs the thing they are reviewing."""
    annotated = _annotate_escalation(
        _out('{"summary": "the spec"}'), "designer", _state(validate_ok=False, critique=CRITIQUE)
    )

    assert _text(annotated).startswith('{"summary": "the spec"}')


def test_a_clean_run_is_left_alone() -> None:
    """A passing artifact must not grow a banner — that would train reviewers to ignore it."""
    annotated = _annotate_escalation(_out(), "designer", _state())

    assert _text(annotated) == '{"summary": "ok"}'


def test_a_judge_failure_is_named_too() -> None:
    annotated = _annotate_escalation(
        _out(), "designer", _state(judge={"passed": False}, retries=1, critique="unsupported claim")
    )

    assert "judge failed after 1 retry" in _text(annotated)


def test_several_failed_layers_are_all_named() -> None:
    annotated = _annotate_escalation(
        _out(),
        "designer",
        _state(validate_ok=False, judge={"passed": False}, retries=2, critique="x"),
    )

    assert "validate and judge failed" in _text(annotated)


def test_a_failure_with_no_critique_still_annotates() -> None:
    """A layer that failed without producing text is still worth flagging."""
    annotated = _annotate_escalation(_out(), "designer", _state(validate_ok=False, retries=2))

    assert "FUNNEL GATE ESCALATED" in _text(annotated)


def test_other_result_keys_survive() -> None:
    """The gated node's output carries more than the text — a diff, usage, node errors."""
    out = {**_out(), "diff": "--- a/x\n+++ b/x"}

    annotated = _annotate_escalation(out, "designer", _state(validate_ok=False, critique="x"))

    assert annotated["diff"] == "--- a/x\n+++ b/x"


# ---- and the audit record is actionable ---------------------------------------------------------


@pytest.mark.asyncio
async def test_the_audit_payload_carries_the_critique() -> None:
    """`failed_layers: ["validate"]` alone left a reader re-running the validator by hand."""
    captured: list[dict[str, Any]] = []

    class _Gov:
        async def record_event(self, event: Any) -> None:
            captured.append(dict(event.payload))

    approver = build_advisory_approver(
        governance=_Gov(),
        topology_id="wms-design",
        agent_id="designer",
        gate_id="wms-design:designer",
        declares_approve=True,
    )
    await approver(
        {
            "provenance": {
                "validate_ok": False,
                "judge": {},
                "review": {},
                "retries": 2,
                "escalated": True,
                "critique": CRITIQUE,
            }
        }
    )

    assert captured[0]["failed_layers"] == ["validate"]
    assert "'affected' is a required property" in captured[0]["critique"]
    assert captured[0]["retries"] == 2
    assert captured[0]["escalated"] is True


@pytest.mark.asyncio
async def test_a_clean_run_records_no_critique() -> None:
    captured: list[dict[str, Any]] = []

    class _Gov:
        async def record_event(self, event: Any) -> None:
            captured.append(dict(event.payload))

    approver = build_advisory_approver(
        governance=_Gov(),
        topology_id="t",
        agent_id="designer",
        gate_id="g",
        declares_approve=True,
    )
    await approver({"provenance": {"validate_ok": True, "judge": {}, "review": {}, "retries": 0}})

    assert captured[0]["failed_layers"] == []
    assert captured[0]["critique"] == ""
