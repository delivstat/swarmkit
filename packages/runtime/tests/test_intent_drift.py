"""Tests for intent drift detection (M7)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from swarmkit_runtime.drift import DriftResult, IntentMonitoringConfig, IntentObserver
from swarmkit_runtime.drift._embeddings import cosine_similarity
from swarmkit_runtime.langgraph_compiler._drift import _handle_drift_result, _is_error_passthrough
from swarmkit_runtime.telemetry._metrics import record_drift_breach, record_drift_score


class TestIntentMonitoringConfig:
    def test_defaults(self) -> None:
        config = IntentMonitoringConfig()
        assert config.enabled is False
        assert config.threshold == 0.75
        assert config.on_drift == "log"

    def test_from_dict(self) -> None:
        config = IntentMonitoringConfig.from_dict(
            {"enabled": True, "threshold": 0.3, "on_drift": "nudge"}
        )
        assert config.enabled is True
        assert config.threshold == 0.3
        assert config.on_drift == "nudge"

    def test_from_none(self) -> None:
        config = IntentMonitoringConfig.from_dict(None)
        assert config.enabled is False

    def test_from_empty_dict(self) -> None:
        config = IntentMonitoringConfig.from_dict({})
        assert config.enabled is False


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector(self) -> None:
        a = [0.0, 0.0]
        b = [1.0, 1.0]
        assert cosine_similarity(a, b) == 0.0

    def test_different_lengths(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0]
        result = cosine_similarity(a, b)
        assert isinstance(result, float)


class TestIntentObserver:
    def test_disabled_returns_zero_drift(self) -> None:
        config = IntentMonitoringConfig(enabled=False)
        observer = IntentObserver(config)
        observer.set_anchor("test goal")
        result = observer.observe(step=1, output="any output")
        assert result.score == 0.0
        assert result.exceeded is False
        assert result.action_taken is None

    def test_no_anchor_returns_zero_drift(self) -> None:
        config = IntentMonitoringConfig(enabled=True)
        observer = IntentObserver(config)
        result = observer.observe(step=1, output="any output")
        assert result.score == 0.0
        assert result.exceeded is False

    def test_identical_output_zero_drift(self) -> None:
        config = IntentMonitoringConfig(enabled=True, threshold=0.5)
        observer = IntentObserver(config)
        observer.set_anchor("Review the code for security vulnerabilities")
        result = observer.observe(step=1, output="Review the code for security vulnerabilities")
        assert result.score < 0.01
        assert result.exceeded is False

    def test_different_output_nonzero_drift(self) -> None:
        config = IntentMonitoringConfig(enabled=True, threshold=0.01)
        observer = IntentObserver(config)
        observer.set_anchor("Review the code for security vulnerabilities")
        result = observer.observe(
            step=1, output="The weather today is sunny with clear skies and mild temperatures"
        )
        assert result.score > 0.01
        assert result.exceeded is True
        assert result.action_taken == "log"

    def test_nudge_strategy(self) -> None:
        config = IntentMonitoringConfig(enabled=True, threshold=0.01, on_drift="nudge")
        observer = IntentObserver(config)
        observer.set_anchor("Review the code for security vulnerabilities")
        result = observer.observe(
            step=1, output="The weather today is sunny with clear skies and mild temperatures"
        )
        assert result.action_taken == "nudge"

    def test_warn_strategy(self) -> None:
        config = IntentMonitoringConfig(enabled=True, threshold=0.01, on_drift="warn")
        observer = IntentObserver(config)
        observer.set_anchor("Review the code for security vulnerabilities")
        result = observer.observe(
            step=1, output="The weather today is sunny with clear skies and mild temperatures"
        )
        assert result.action_taken == "warn"

    def test_history_tracking(self) -> None:
        config = IntentMonitoringConfig(enabled=True, threshold=0.5)
        observer = IntentObserver(config)
        observer.set_anchor("Code review")
        observer.observe(step=1, output="Found a bug")
        observer.observe(step=2, output="Fixed the bug")
        observer.observe(step=3, output="All tests pass")
        assert len(observer.history) == 3

    def test_nudge_message(self) -> None:
        config = IntentMonitoringConfig(enabled=True)
        observer = IntentObserver(config)
        observer.set_anchor("Review code for security")
        msg = observer.get_nudge_message()
        assert "Review code for security" in msg
        assert "drifting" in msg.lower()

    def test_drift_result_fields(self) -> None:
        result = DriftResult(score=0.35, threshold=0.25, exceeded=True, action_taken="nudge")
        assert result.score == 0.35
        assert result.threshold == 0.25
        assert result.exceeded is True
        assert result.action_taken == "nudge"


import pytest  # noqa: E402


class TestDriftMetrics:
    def test_record_drift_score_callable(self) -> None:
        """record_drift_score should be callable without error (no-op when metrics not init'd)."""
        record_drift_score(agent_id="test-agent", score=0.42)

    def test_record_drift_breach_callable(self) -> None:
        """record_drift_breach should be callable without error (no-op when metrics not init'd)."""
        record_drift_breach(agent_id="test-agent")


class TestToolErrorSkipped:
    def test_error_prefix_detected(self) -> None:
        assert _is_error_passthrough("Error: connection refused") is True

    def test_tool_error_prefix_detected(self) -> None:
        assert _is_error_passthrough("Tool error: invalid argument") is True

    def test_tool_error_with_leading_whitespace(self) -> None:
        assert _is_error_passthrough("  Error: something broke") is True

    def test_normal_output_not_detected(self) -> None:
        assert _is_error_passthrough("The code review found 3 issues") is False

    def test_error_in_middle_not_detected(self) -> None:
        assert _is_error_passthrough("I found an Error: in the logs") is False

    def test_empty_string(self) -> None:
        assert _is_error_passthrough("") is False


class TestOtelSpanEventOnDrift:
    @pytest.mark.asyncio
    async def test_span_event_added_on_drift(self) -> None:
        """Verify _handle_drift_result adds an OTel span event with drift attributes."""
        mock_span = MagicMock()
        mock_governance = MagicMock()

        async def _noop_record(*a: object, **kw: object) -> None:
            pass

        mock_governance.record_event = _noop_record

        drift_result = DriftResult(score=0.85, threshold=0.75, exceeded=True, action_taken="warn")
        mock_observer = MagicMock()

        with patch("swarmkit_runtime.langgraph_compiler._drift.trace") as mock_trace:
            mock_trace.get_current_span.return_value = mock_span
            await _handle_drift_result(
                drift_result, mock_observer, mock_governance, "test-agent", []
            )

        mock_span.add_event.assert_called_once_with(
            "intent.drift",
            attributes={
                "swarmkit.drift.score": 0.85,
                "swarmkit.drift.threshold": 0.75,
                "swarmkit.drift.action": "warn",
                "swarmkit.drift.exceeded": True,
            },
        )
