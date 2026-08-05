"""`GET /audit` returns what an event was for, not just its header.

The audit store has persisted the full M6 event since M6 — policy decision, inputs, outputs,
verdict, reasoning, confidence, model, tokens, cost, duration, error: twenty-four columns, written
on the way in and read back on the way out. `_audit_event_to_dict` serialized nine of them.

So the UI's audit table was not hiding detail; it was never sent any. A reader could see THAT
`skill.executed` happened and never what the skill was asked or what it answered — which is most of
why anyone opens the log. Same shape as the rest of this week: the information exists, nothing
surfaces it, and the absence renders as an ordinary-looking row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.server._routes_introspection import _audit_event_to_dict


def _event(**over: object) -> AuditEvent:
    fields: dict[str, object] = {
        "event_type": "skill.executed",
        "agent_id": "triage",
        "timestamp": datetime.now(tz=UTC),
        "skill_id": "search-wms-tables",
    }
    fields.update(over)
    return AuditEvent(**fields)  # type: ignore[arg-type]


def test_the_governance_decision_is_returned() -> None:
    """Whether a call was allowed is the whole point of a governance record, and it was the
    single most conspicuous omission."""
    out = _audit_event_to_dict(_event(policy_decision="allow", policy_reason="granted by role"))

    assert out["policy_decision"] == "allow"
    assert out["policy_reason"] == "granted by role"


def test_the_inputs_and_outputs_are_returned() -> None:
    """What the tool was asked and what came back — without these a row says a skill ran and
    nothing more."""
    out = _audit_event_to_dict(
        _event(inputs={"query": "PGM"}, outputs={"result": "3 tables"}),
    )

    assert out["inputs"] == {"query": "PGM"}
    assert out["outputs"] == {"result": "3 tables"}


def test_a_decision_skills_verdict_is_returned() -> None:
    out = _audit_event_to_dict(
        _event(verdict="fail", reasoning="no grounding cited", confidence=0.82),
    )

    assert out["verdict"] == "fail"
    assert out["reasoning"] == "no grounding cited"
    assert out["confidence"] == 0.82


def test_usage_and_timing_are_returned() -> None:
    out = _audit_event_to_dict(
        _event(
            model_provider="anthropic",
            model_name="claude-opus-5",
            tokens_in=1200,
            tokens_out=340,
            cost_usd=0.42,
            duration_ms=1250,
        ),
    )

    assert out["model_name"] == "claude-opus-5"
    assert out["tokens_in"] == 1200
    assert out["cost_usd"] == 0.42
    assert out["duration_ms"] == 1250


def test_an_error_is_returned() -> None:
    out = _audit_event_to_dict(_event(error={"message": "timed out"}))

    assert out["error"] == {"message": "timed out"}


def test_an_unrecorded_field_is_null_not_absent() -> None:
    """Null says "never recorded". A missing key would make the client guess, and the guess it
    would make — treating absence as allowed — is exactly the wrong one."""
    out = _audit_event_to_dict(_event())

    assert "policy_decision" in out
    assert out["policy_decision"] is None
    assert out["duration_ms"] is None


def test_the_parent_event_id_is_json_serializable() -> None:
    """It is a UUID on the dataclass, and a UUID is not JSON — FastAPI would 500 on the whole
    request, so one linked event would break the entire log."""
    parent = uuid4()

    out = _audit_event_to_dict(_event(parent_event_id=parent))

    assert out["parent_event_id"] == str(parent)


def test_the_header_fields_are_unchanged() -> None:
    """Existing readers keep working — this adds keys, it does not rename any."""
    event = _event(run_id="WMS-5:design", topology_id="wms-triage", agent_role="worker")

    out = _audit_event_to_dict(event)

    assert out["event_type"] == "skill.executed"
    assert out["agent_id"] == "triage"
    assert out["skill_id"] == "search-wms-tables"
    assert out["run_id"] == "WMS-5:design"
    assert out["topology_id"] == "wms-triage"
    assert out["agent_role"] == "worker"
    assert out["event_id"] == str(event.event_id)


def test_every_persisted_column_reaches_the_client() -> None:
    """The property, rather than a list that drifts: anything the store has a column for is
    something a reader can see. Stated against the table definition so a new column that is
    persisted but never surfaced fails here instead of going unnoticed."""
    from swarmkit_runtime.audit._tables import audit_events  # noqa: PLC0415

    out = _audit_event_to_dict(_event())
    missing = {c.name for c in audit_events.columns} - set(out)

    assert not missing, f"persisted but never returned: {sorted(missing)}"
