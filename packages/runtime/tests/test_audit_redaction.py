"""Tests for per-skill audit redaction (M6 PR 12)."""

from __future__ import annotations

from types import SimpleNamespace

from swarmkit_runtime.audit import apply_audit_policy, resolve_audit_config


class TestApplyAuditPolicy:
    def test_full_with_no_redaction(self) -> None:
        data = {"query": "hello", "context": "world"}
        result = apply_audit_policy(data, field="inputs", log_level="full")
        assert result == {"query": "hello", "context": "world"}

    def test_full_with_redaction(self) -> None:
        data = {"query": "hello", "password": "secret"}
        result = apply_audit_policy(
            data, field="inputs", log_level="full", redact_paths=["$.password"]
        )
        assert result is not None
        assert result["query"] == "hello"
        assert result["password"] == "[REDACTED]"

    def test_summary_truncates(self) -> None:
        data = {"long_text": "x" * 500, "short": "hi"}
        result = apply_audit_policy(data, field="inputs", log_level="summary")
        assert result is not None
        assert len(result["long_text"]) < 500
        assert "..." in result["long_text"]
        assert result["short"] == "hi"

    def test_none_returns_none(self) -> None:
        data = {"query": "hello"}
        result = apply_audit_policy(data, field="inputs", log_level="none")
        assert result is None

    def test_summary_with_redaction(self) -> None:
        data = {"query": "hello", "api_key": "sk-xxx"}
        result = apply_audit_policy(
            data, field="inputs", log_level="summary", redact_paths=["$.api_key"]
        )
        assert result is not None
        assert result["api_key"] == "[REDACTED]"


class TestResolveAuditConfig:
    def test_category_defaults_capability(self) -> None:
        log_in, log_out, redact = resolve_audit_config(None, "capability")
        assert log_in == "summary"
        assert log_out == "summary"
        assert redact == []

    def test_category_defaults_decision(self) -> None:
        log_in, log_out, _redact = resolve_audit_config(None, "decision")
        assert log_in == "summary"
        assert log_out == "full"

    def test_category_defaults_persistence(self) -> None:
        _log_in, log_out, _redact = resolve_audit_config(None, "persistence")
        assert log_out == "none"

    def test_skill_audit_overrides_defaults(self) -> None:
        audit = SimpleNamespace(log_inputs="full", log_outputs="none", redact=["$.secret"])
        log_in, log_out, redact = resolve_audit_config(audit, "capability")
        assert log_in == "full"
        assert log_out == "none"
        assert redact == ["$.secret"]

    def test_workspace_level_clamps_down(self) -> None:
        audit = SimpleNamespace(log_inputs="full", log_outputs="full", redact=[])
        log_in, log_out, _ = resolve_audit_config(audit, "decision", workspace_level="minimal")
        assert log_in == "none"
        assert log_out == "none"

    def test_workspace_standard_clamps_full_to_summary(self) -> None:
        audit = SimpleNamespace(log_inputs="full", log_outputs="full", redact=[])
        log_in, log_out, _ = resolve_audit_config(audit, "decision", workspace_level="standard")
        assert log_in == "summary"
        assert log_out == "summary"

    def test_workspace_detailed_allows_full(self) -> None:
        audit = SimpleNamespace(log_inputs="full", log_outputs="full", redact=[])
        log_in, log_out, _ = resolve_audit_config(audit, "decision", workspace_level="detailed")
        assert log_in == "full"
        assert log_out == "full"

    def test_no_workspace_level_no_clamping(self) -> None:
        audit = SimpleNamespace(log_inputs="full", log_outputs="full", redact=[])
        log_in, log_out, _ = resolve_audit_config(audit, "decision", workspace_level=None)
        assert log_in == "full"
        assert log_out == "full"

    def test_none_category_falls_back_to_capability(self) -> None:
        log_in, log_out, _ = resolve_audit_config(None, None)
        assert log_in == "summary"
        assert log_out == "summary"
