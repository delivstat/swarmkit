"""The `topology.run` span's `swarmkit.run.llm_calls` counts every model call, as `swarmkit trace`
does. `RunTrace.llm_calls` only counts the extra calls (tool loop, synthesis) — each agent step is
recorded separately — so the span said `0` for a one-agent run that spent 215 tokens."""

from __future__ import annotations

from swarmkit_runtime._workspace_runtime import _run_trace_to_span
from swarmkit_runtime.trace import AgentStep, RunTrace


def test_span_llm_calls_matches_the_trace_summary() -> None:
    trace = RunTrace(run_id="r", topology="hello", start_time=1.0)
    trace.add_step(
        AgentStep(
            agent_id="a", model="m", role="root", start_time=1.0, input_tokens=10, output_tokens=5
        )
    )
    trace.end_time = 2.0
    span = _run_trace_to_span(trace, "ws")
    assert span.attributes["swarmkit.run.llm_calls"] == 1
    assert "across 1 LLM call(s)" in trace.render_text()
