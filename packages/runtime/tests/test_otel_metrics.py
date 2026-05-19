"""Tests for OTel metrics (M6 PR 7)."""

from __future__ import annotations

from swarmkit_runtime.telemetry import (
    init_metrics,
    record_agent_step,
    record_approval_wait,
    record_governance_decision,
    record_run_completed,
    record_run_started,
    record_tool_call,
)


class TestMetricsInit:
    def test_init_creates_instruments(self) -> None:
        init_metrics("swarmkit-test")

        from swarmkit_runtime.telemetry import _metrics  # noqa: PLC0415

        assert _metrics._meter is not None
        assert _metrics._runs_total is not None
        assert _metrics._agent_steps_total is not None
        assert _metrics._tool_calls_total is not None
        assert _metrics._governance_decisions_total is not None
        assert _metrics._run_duration_ms is not None
        assert _metrics._tool_duration_ms is not None
        assert _metrics._approval_wait_ms is not None


class TestMetricsRecording:
    def test_record_run_started_no_error(self) -> None:
        init_metrics()
        record_run_started(topology_id="hello")

    def test_record_run_completed_no_error(self) -> None:
        init_metrics()
        record_run_completed(topology_id="hello", duration_ms=5000)

    def test_record_agent_step_no_error(self) -> None:
        init_metrics()
        record_agent_step(agent_id="greeter", topology_id="hello")

    def test_record_tool_call_no_error(self) -> None:
        init_metrics()
        record_tool_call(tool_name="say-hello", status="success", duration_ms=150)

    def test_record_tool_call_without_duration(self) -> None:
        init_metrics()
        record_tool_call(tool_name="say-hello", status="error")

    def test_record_governance_decision_no_error(self) -> None:
        init_metrics()
        record_governance_decision(decision="allow", scope="code:read")

    def test_record_approval_wait_no_error(self) -> None:
        init_metrics()
        record_approval_wait(scope="deploy:prod", wait_ms=30000)


class TestMetricsBeforeInit:
    def test_record_before_init_no_crash(self) -> None:
        from swarmkit_runtime.telemetry import _metrics  # noqa: PLC0415

        _metrics._runs_total = None
        _metrics._agent_steps_total = None
        _metrics._tool_calls_total = None
        _metrics._governance_decisions_total = None
        _metrics._run_duration_ms = None
        _metrics._tool_duration_ms = None
        _metrics._approval_wait_ms = None

        record_run_started(topology_id="t")
        record_run_completed(topology_id="t", duration_ms=100)
        record_agent_step(agent_id="a", topology_id="t")
        record_tool_call(tool_name="t", status="ok")
        record_governance_decision(decision="allow")
        record_approval_wait(scope="s", wait_ms=100)
