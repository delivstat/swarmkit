"""OTel trace export (design: runtime/otel-trace-export).

Covers the facade emitting a recorded span tree with accurate timestamps/nesting into an in-memory
exporter, the RunTrace→RecordedSpan conversion, and the disabled no-op.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from swarmkit_runtime._workspace_runtime import _run_trace_to_span
from swarmkit_runtime.telemetry import (
    RecordedSpan,
    SwarmKitTelemetry,
    TelemetryConfig,
    get_telemetry,
)
from swarmkit_runtime.trace import AgentStep, RunTrace, ToolCall


def _capturing_telemetry() -> tuple[SwarmKitTelemetry, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    cfg = TelemetryConfig(enabled=True, exporter="otlp")
    return SwarmKitTelemetry(cfg, provider=provider), exporter


def test_export_emits_nested_span_tree_with_timestamps_and_attributes() -> None:
    tel, exporter = _capturing_telemetry()
    root = RecordedSpan(
        name="topology.run",
        start_ns=1_000_000_000,
        end_ns=5_000_000_000,
        attributes={
            "swarmkit.topology.id": "single-agent-design",
            "swarmkit.model.cost_usd": 0.045,
        },
        children=(
            RecordedSpan(
                name="agent.step.architect",
                start_ns=1_500_000_000,
                end_ns=4_500_000_000,
                attributes={"swarmkit.agent.id": "architect", "swarmkit.model.tokens_in": 4321},
                children=(
                    RecordedSpan(
                        name="tool.call.get-jira-issue",
                        start_ns=1_600_000_000,
                        end_ns=1_900_000_000,
                        attributes={"swarmkit.tool.name": "get-jira-issue"},
                    ),
                ),
            ),
        ),
    )
    tel.export_run_spans(root)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert set(spans) == {"topology.run", "agent.step.architect", "tool.call.get-jira-issue"}
    run, step, tool = (
        spans["topology.run"],
        spans["agent.step.architect"],
        spans["tool.call.get-jira-issue"],
    )

    # recorded timestamps preserved (accurate Jaeger timeline)
    assert run.start_time == 1_000_000_000 and run.end_time == 5_000_000_000
    assert tool.start_time == 1_600_000_000 and tool.end_time == 1_900_000_000
    # attributes carried
    assert run.attributes is not None and step.attributes is not None
    assert run.attributes["swarmkit.model.cost_usd"] == 0.045
    assert step.attributes["swarmkit.model.tokens_in"] == 4321
    # nesting: same trace, correct parent chain
    assert run.context is not None and step.context is not None and tool.context is not None
    assert step.parent is not None and tool.parent is not None
    assert run.context.trace_id == step.context.trace_id == tool.context.trace_id
    assert step.parent.span_id == run.context.span_id
    assert tool.parent.span_id == step.context.span_id


def test_error_propagates_as_span_status() -> None:
    tel, exporter = _capturing_telemetry()
    tel.export_run_spans(RecordedSpan(name="tool.call.x", start_ns=0, end_ns=1000, error="boom"))
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert span.status.description == "boom"


def test_disabled_telemetry_is_a_noop() -> None:
    disabled = SwarmKitTelemetry(TelemetryConfig(enabled=False, exporter="none"))
    assert disabled.enabled is False
    # export is a no-op — it must not raise (and there's no exporter to receive anything).
    disabled.export_run_spans(RecordedSpan(name="topology.run", start_ns=0, end_ns=1))


def test_run_trace_conversion_nests_tools_sequentially_in_their_step() -> None:
    trace = RunTrace()
    trace.start("run-1", "single-agent-design")
    step = AgentStep(
        agent_id="architect",
        model="moonshotai/kimi-k2.6",
        role="worker",
        start_time=100.0,
        end_time=110.0,
        input_tokens=4321,
        output_tokens=487,
        cost_usd=0.0045,
        tool_calls=[
            ToolCall(tool_name="search-jira", duration_ms=2000),
            ToolCall(tool_name="get-pipeline", duration_ms=3000, error="timeout"),
        ],
    )
    trace.add_step(step)
    trace.finish()

    root = _run_trace_to_span(trace, "sterling-oms")
    assert root.name == "topology.run"
    assert root.attributes["swarmkit.topology.id"] == "single-agent-design"
    assert root.attributes["swarmkit.workspace.id"] == "sterling-oms"  # identifies the instance
    assert root.attributes["swarmkit.model.cost_usd"] == 0.0045

    (agent,) = root.children
    assert agent.name == "agent.step.architect"
    assert agent.attributes["swarmkit.model.tokens_in"] == 4321
    t1, t2 = agent.children
    # sequential layout inside the step window: t1 [100..102], t2 [102..105]
    assert t1.start_ns == 100 * 1_000_000_000 and t1.end_ns == 102 * 1_000_000_000
    assert t2.start_ns == 102 * 1_000_000_000 and t2.end_ns == 105 * 1_000_000_000
    assert t2.error == "timeout"


def test_get_telemetry_returns_a_singleton() -> None:
    assert get_telemetry() is get_telemetry()
