"""Tests for SwarmKit telemetry module (M6 PR 3)."""
# ruff: noqa: SIM117

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from swarmkit_runtime.telemetry import (
    SwarmKitTelemetry,
    TelemetryConfig,
    load_telemetry_config,
)


class _InMemoryExporter(SpanExporter):
    """Simple in-memory span collector for tests."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: Any) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 0) -> bool:
        return True


@pytest.fixture()
def _reset_tracer() -> None:
    """Reset global tracer provider between tests."""
    trace.set_tracer_provider(TracerProvider())


class TestTelemetryConfig:
    def test_defaults(self) -> None:
        config = TelemetryConfig()
        assert config.enabled is False
        assert config.exporter == "none"
        assert config.send_prompts is False
        assert config.sample_rate == 1.0
        assert config.service_name == "swarmkit"

    def test_load_missing_file(self, tmp_path: None) -> None:
        with patch("swarmkit_runtime.telemetry._config.Path.home", return_value=tmp_path):
            config = load_telemetry_config()
        assert config.enabled is False

    def test_load_from_file(self, tmp_path: object) -> None:
        from pathlib import Path  # noqa: PLC0415

        home = Path(str(tmp_path))
        config_dir = home / ".swarmkit"
        config_dir.mkdir(parents=True)
        (config_dir / "config.yaml").write_text(
            "telemetry:\n  enabled: true\n  exporter: console\n  endpoint: http://rynko.dev/traces\n"
        )
        with patch("swarmkit_runtime.telemetry._config.Path.home", return_value=home):
            config = load_telemetry_config()
        assert config.enabled is True
        assert config.exporter == "console"
        assert config.endpoint == "http://rynko.dev/traces"


class TestSwarmKitTelemetryDisabled:
    def test_noop_when_disabled(self) -> None:
        telemetry = SwarmKitTelemetry(TelemetryConfig(enabled=False))
        assert telemetry.enabled is False
        with telemetry.start_run(topology_id="t", run_id="r") as span:
            assert span is not None

    def test_noop_when_exporter_none(self) -> None:
        telemetry = SwarmKitTelemetry(TelemetryConfig(enabled=True, exporter="none"))
        assert telemetry.enabled is False


@pytest.mark.usefixtures("_reset_tracer")
class TestSwarmKitTelemetryConsole:
    def test_console_exporter_creates_spans(self) -> None:
        config = TelemetryConfig(enabled=True, exporter="console")
        telemetry = SwarmKitTelemetry(config)
        assert telemetry.enabled is True

        with telemetry.start_run(topology_id="code-review", run_id="run-001") as run_span:
            assert run_span.is_recording()
            with telemetry.start_agent_step(
                agent_id="reviewer", step=1, archetype="code-analyst", role="worker"
            ) as agent_span:
                assert agent_span.is_recording()
                with telemetry.start_tool_call(
                    tool_name="github-pr-read", server_id="github"
                ) as tool_span:
                    telemetry.record_tool_result(tool_span, status="success")

    def test_governance_decision_event(self) -> None:
        config = TelemetryConfig(enabled=True, exporter="console")
        telemetry = SwarmKitTelemetry(config)

        with telemetry.start_run(topology_id="t", run_id="r"):
            with telemetry.start_agent_step(agent_id="a", step=1):
                telemetry.record_governance_decision(
                    decision="allow", policy="default", scope="code:read"
                )

    def test_model_usage_attributes(self) -> None:
        config = TelemetryConfig(enabled=True, exporter="console")
        telemetry = SwarmKitTelemetry(config)

        with telemetry.start_run(topology_id="t", run_id="r"):
            with telemetry.start_agent_step(agent_id="a", step=1) as span:
                telemetry.record_model_usage(
                    span,
                    provider="anthropic",
                    model="claude-sonnet-4-6",
                    tokens_in=1500,
                    tokens_out=200,
                    cost_usd=0.003,
                )

    def test_drift_event(self) -> None:
        config = TelemetryConfig(enabled=True, exporter="console")
        telemetry = SwarmKitTelemetry(config)

        with telemetry.start_run(topology_id="t", run_id="r"):
            with telemetry.start_agent_step(agent_id="a", step=3):
                telemetry.record_drift(score=0.35, threshold=0.25, action="nudge", exceeded=True)

    def test_error_recording(self) -> None:
        config = TelemetryConfig(enabled=True, exporter="console")
        telemetry = SwarmKitTelemetry(config)

        with telemetry.start_run(topology_id="t", run_id="r"):
            with telemetry.start_agent_step(agent_id="a", step=1) as span:
                telemetry.record_error(span, error=ValueError("test error"))


class TestSpanHierarchy:
    def test_spans_nested_correctly(self) -> None:
        exporter = _InMemoryExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        tracer = provider.get_tracer("swarmkit-test")

        config = TelemetryConfig(enabled=True, exporter="console")
        telemetry = SwarmKitTelemetry.__new__(SwarmKitTelemetry)
        telemetry._config = config
        telemetry._tracer = tracer

        with telemetry.start_run(topology_id="topo", run_id="run-x") as _run_span:
            with telemetry.start_agent_step(agent_id="agent-1", step=1) as _agent_span:
                with telemetry.start_tool_call(tool_name="tool-a") as _tool_span:
                    telemetry.record_tool_result(_tool_span, status="success")

        provider.force_flush()
        spans = exporter.spans
        assert len(spans) == 3

        tool_span = spans[0]
        agent_span = spans[1]
        run_span = spans[2]

        assert tool_span.name == "tool.call.tool-a"
        assert agent_span.name == "agent.step.agent-1"
        assert run_span.name == "topology.run"

        assert tool_span.parent is not None
        assert tool_span.parent.span_id == agent_span.context.span_id
        assert agent_span.parent is not None
        assert agent_span.parent.span_id == run_span.context.span_id

        attrs = run_span.attributes or {}
        assert attrs.get("swarmkit.topology.id") == "topo"
        assert attrs.get("swarmkit.run.id") == "run-x"
        agent_attrs = agent_span.attributes or {}
        assert agent_attrs.get("swarmkit.agent.id") == "agent-1"
        assert agent_attrs.get("swarmkit.agent.step") == 1
        tool_attrs = tool_span.attributes or {}
        assert tool_attrs.get("swarmkit.tool.name") == "tool-a"
        assert tool_attrs.get("swarmkit.tool.status") == "success"
