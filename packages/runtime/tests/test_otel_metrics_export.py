"""Tests that SwarmKit metrics actually *flow* to a reader (design: runtime/otel-metrics-export).

The older test_otel_metrics.py only asserts record_* doesn't crash — which passes even against a
no-op meter, so it never caught that the runtime exported zero metrics. These tests bind the
instruments to a real MeterProvider + InMemoryMetricReader and assert the data points are emitted,
exercise the RunTrace→metrics bridge, and pin the endpoint derivation.
"""

from __future__ import annotations

from typing import Any

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from swarmkit_runtime.telemetry._config import TelemetryConfig
from swarmkit_runtime.telemetry._tracer import SwarmKitTelemetry, _metrics_endpoint
from swarmkit_runtime.trace import AgentStep, RunTrace, ToolCall


def _capturing_telemetry() -> tuple[SwarmKitTelemetry, InMemoryMetricReader]:
    """A telemetry facade whose metrics land in an in-memory reader (enabled, otlp)."""
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    cfg = TelemetryConfig(enabled=True, exporter="otlp", service_name="svc-test")
    telemetry = SwarmKitTelemetry(cfg, provider=TracerProvider(), meter_provider=meter_provider)
    return telemetry, reader


def _sum_by_name(reader: InMemoryMetricReader) -> dict[str, float]:
    """Map metric name → summed data-point value (counters) / count (histograms)."""
    out: dict[str, float] = {}
    data = reader.get_metrics_data()
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                total = 0.0
                for point in metric.data.data_points:
                    total += float(getattr(point, "value", getattr(point, "count", 0)))
                out[metric.name] = out.get(metric.name, 0.0) + total
    return out


def _trace_with(steps_tools: list[list[Any]]) -> RunTrace:
    trace = RunTrace(run_id="r1", topology="single-agent-design", duration_ms=4200)
    for i, tools in enumerate(steps_tools):
        trace.agent_steps.append(
            AgentStep(agent_id=f"agent-{i}", duration_ms=1000, tool_calls=list(tools))
        )
    return trace


def test_export_run_metrics_emits_run_step_and_tool_counts() -> None:
    telemetry, reader = _capturing_telemetry()
    trace = _trace_with(
        [
            [
                ToolCall(tool_name="search-jira", duration_ms=2000),
                ToolCall(tool_name="get-pipeline", duration_ms=3000, error="timeout"),
            ],
            [ToolCall(tool_name="say-hello", duration_ms=150)],
        ]
    )

    telemetry.export_run_metrics(trace)

    totals = _sum_by_name(reader)
    assert totals["swarmkit.runs.total"] == 1
    assert totals["swarmkit.agent.steps.total"] == 2
    assert totals["swarmkit.tool.calls.total"] == 3
    # Durations recorded as histograms: 1 run, 3 tool calls.
    assert totals["swarmkit.runs.duration_ms"] == 1
    assert totals["swarmkit.tool.duration_ms"] == 3


def test_tool_call_error_is_labelled_error() -> None:
    telemetry, reader = _capturing_telemetry()
    telemetry.export_run_metrics(
        _trace_with([[ToolCall(tool_name="boom", duration_ms=1, error="kaboom")]])
    )

    data = reader.get_metrics_data()
    assert data is not None
    statuses = {
        (point.attributes or {}).get("status")
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "swarmkit.tool.calls.total"
        for point in metric.data.data_points
    }
    assert statuses == {"error"}


def test_disabled_telemetry_export_run_metrics_is_a_noop() -> None:
    disabled = SwarmKitTelemetry(TelemetryConfig(enabled=False, exporter="none"))
    # Must not raise and must not require a meter provider.
    disabled.export_run_metrics(_trace_with([[ToolCall(tool_name="x")]]))


def test_metrics_endpoint_derives_from_traces_endpoint() -> None:
    assert (
        _metrics_endpoint("http://localhost:4318/v1/traces") == "http://localhost:4318/v1/metrics"
    )
    # Non-standard endpoints are returned unchanged.
    assert _metrics_endpoint("http://collector:9999/custom") == "http://collector:9999/custom"
