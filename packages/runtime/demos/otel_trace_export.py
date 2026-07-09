"""Demo: OTel trace export (design: runtime/otel-trace-export).

Before this, `SWARMKIT_OTEL_EXPORTER=otlp` was a no-op — the runtime never emitted spans. This shows
the real path: a finished `RunTrace` is converted to an OTel span tree and exported. Here the
console exporter prints the spans so you can see them without a collector; in `serve` with
`SWARMKIT_OTEL_EXPORTER=otlp` the same spans go to the collector and show up in Jaeger.

Run it:

    uv run python packages/runtime/demos/otel_trace_export.py
"""

from __future__ import annotations

from swarmkit_runtime._workspace_runtime import _run_trace_to_span
from swarmkit_runtime.telemetry import SwarmKitTelemetry, TelemetryConfig
from swarmkit_runtime.trace import AgentStep, RunTrace, ToolCall


def main() -> None:
    # A representative finished run: one architect agent that made two tool calls. Times are fixed
    # (epoch seconds) so the printed waterfall is stable — a real run uses wall-clock timestamps.
    trace = RunTrace()
    trace.start("demo-run-1", "single-agent-design")
    trace.start_time = 100.0
    trace.add_step(
        AgentStep(
            agent_id="architect",
            model="moonshotai/kimi-k2.6",
            role="worker",
            start_time=100.0,
            end_time=112.0,
            input_tokens=4321,
            output_tokens=487,
            cost_usd=0.0045,
            tool_calls=[
                ToolCall(tool_name="search-jira", duration_ms=2000),
                ToolCall(tool_name="get-pipeline", duration_ms=3000),
            ],
        )
    )
    trace.finish()
    trace.end_time = 112.0  # fix the run's end so the waterfall spans one window

    print("Converting the finished RunTrace to an OTel span tree and exporting (console):\n")
    telemetry = SwarmKitTelemetry(TelemetryConfig(enabled=True, exporter="console"))
    telemetry.export_run_spans(_run_trace_to_span(trace, "sterling-oms"))
    print(
        "\nEach span above carries its recorded start/end + swarmkit.* attributes (tokens, cost).\n"
        "With SWARMKIT_OTEL_EXPORTER=otlp these go to the collector → Jaeger renders the waterfall."
    )


if __name__ == "__main__":
    main()
