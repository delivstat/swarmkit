"""Tests for expanded AuditEvent schema and redaction utilities (M6 PR 1)."""

from datetime import UTC, datetime
from uuid import UUID

from swarmkit_runtime.governance import (
    AuditEvent,
    hitl_requested_event,
    hitl_resolved_event,
    redact_json_pointers,
    run_ended_event,
    run_started_event,
    summarize_value,
)


class TestAuditEventExpanded:
    def test_backward_compat_minimal(self):
        event = AuditEvent(
            event_type="policy.denied",
            agent_id="worker-1",
            timestamp=datetime.now(tz=UTC),
            payload={"reason": "scope denied"},
        )
        assert event.event_type == "policy.denied"
        assert event.agent_id == "worker-1"
        assert event.run_id is None
        assert event.verdict is None
        assert event.tokens_in is None
        assert isinstance(event.event_id, UUID)

    def test_full_fields(self):
        event = AuditEvent(
            event_type="agent.completed",
            agent_id="code-reviewer",
            timestamp=datetime.now(tz=UTC),
            run_id="run-abc-123",
            agent_role="worker",
            skill_id="code-quality-review",
            skill_category="decision",
            verdict="pass",
            reasoning="Code follows best practices",
            confidence=0.92,
            model_provider="anthropic",
            model_name="claude-sonnet-4-6",
            tokens_in=1500,
            tokens_out=200,
            cost_usd=0.003,
            duration_ms=2400,
            policy_decision="allow",
        )
        assert event.run_id == "run-abc-123"
        assert event.agent_role == "worker"
        assert event.skill_category == "decision"
        assert event.verdict == "pass"
        assert event.confidence == 0.92
        assert event.tokens_in == 1500
        assert event.cost_usd == 0.003
        assert event.duration_ms == 2400

    def test_event_id_unique(self):
        e1 = AuditEvent(
            event_type="test", agent_id="a", timestamp=datetime.now(tz=UTC)
        )
        e2 = AuditEvent(
            event_type="test", agent_id="a", timestamp=datetime.now(tz=UTC)
        )
        assert e1.event_id != e2.event_id

    def test_frozen(self):
        event = AuditEvent(
            event_type="test", agent_id="a", timestamp=datetime.now(tz=UTC)
        )
        try:
            event.agent_id = "b"  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass


class TestRedaction:
    def test_redact_top_level(self):
        data = {"name": "Alice", "password": "secret123", "role": "admin"}
        result = redact_json_pointers(data, ["$.password"])
        assert result["password"] == "[REDACTED]"
        assert result["name"] == "Alice"
        assert result["role"] == "admin"

    def test_redact_multiple(self):
        data = {"api_key": "sk-xxx", "token": "tok-yyy", "value": 42}
        result = redact_json_pointers(data, ["$.api_key", "$.token"])
        assert result["api_key"] == "[REDACTED]"
        assert result["token"] == "[REDACTED]"
        assert result["value"] == 42

    def test_redact_missing_path(self):
        data = {"name": "Alice"}
        result = redact_json_pointers(data, ["$.nonexistent"])
        assert result == {"name": "Alice"}

    def test_redact_empty_paths(self):
        data = {"secret": "val"}
        result = redact_json_pointers(data, [])
        assert result == {"secret": "val"}

    def test_redact_empty_obj(self):
        result = redact_json_pointers({}, ["$.x"])
        assert result == {}

    def test_redact_without_dollar_prefix(self):
        data = {"password": "secret"}
        result = redact_json_pointers(data, ["password"])
        assert result["password"] == "[REDACTED]"


class TestSummarize:
    def test_short_value(self):
        assert summarize_value("hello") == "hello"

    def test_long_value_truncated(self):
        long_text = "x" * 500
        result = summarize_value(long_text)
        assert len(result) < 500
        assert result.startswith("x" * 200)
        assert "500 chars" in result

    def test_non_string(self):
        result = summarize_value({"key": "value"})
        assert "key" in result


class TestWorkspaceEvents:
    def test_run_started(self):
        event = run_started_event(
            run_id="run-001",
            topology_id="code-review",
            trigger_source="cli",
            inputs={"pr": "#49"},
        )
        assert event.event_type == "run.started"
        assert event.agent_id == "runtime"
        assert event.run_id == "run-001"
        assert event.topology_id == "code-review"
        assert event.inputs == {"pr": "#49"}
        assert event.payload["trigger_source"] == "cli"

    def test_run_ended_success(self):
        event = run_ended_event(
            run_id="run-001",
            topology_id="code-review",
            status="success",
            duration_ms=5000,
            total_cost_usd=0.15,
        )
        assert event.event_type == "run.ended"
        assert event.duration_ms == 5000
        assert event.cost_usd == 0.15
        assert event.payload["status"] == "success"
        assert event.error is None

    def test_run_ended_error(self):
        event = run_ended_event(
            run_id="run-002",
            topology_id="hello",
            status="error",
            duration_ms=1200,
            error={"type": "TimeoutError", "message": "Agent timed out"},
        )
        assert event.payload["status"] == "error"
        assert event.error is not None
        assert event.error["type"] == "TimeoutError"

    def test_hitl_requested(self):
        event = hitl_requested_event(
            run_id="run-001",
            agent_id="resolution-agent",
            review_queue_id="rq-42",
            summary="High-value order requires manual approval",
        )
        assert event.event_type == "hitl.requested"
        assert event.agent_id == "resolution-agent"
        assert event.payload["review_queue_id"] == "rq-42"
        assert event.payload["summary"] == "High-value order requires manual approval"

    def test_hitl_resolved(self):
        event = hitl_resolved_event(
            run_id="run-001",
            agent_id="resolution-agent",
            decision="approved",
            by_user="srijith",
        )
        assert event.event_type == "hitl.resolved"
        assert event.payload["decision"] == "approved"
        assert event.payload["by_user"] == "srijith"
