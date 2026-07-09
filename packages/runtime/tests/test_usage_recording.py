"""Usage recording write-path + trace cost accumulation (design: runtime/usage-recording-and-cost).

The runtime captured usage into the in-memory trace but never wrote it to the store, so /usage and
/jobs/history always reported zero. These cover the two halves now wired: the trace accumulating
per-call cost, and `_record_run_usage` persisting a completed run into both sinks.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit_runtime._workspace_runtime import RunResult, UsageSummary
from swarmkit_runtime.persistence import SqliteStore
from swarmkit_runtime.server._jobs import _record_run_usage
from swarmkit_runtime.trace import AgentStep, RunTrace


def test_trace_accumulates_cost_across_calls() -> None:
    trace = RunTrace()
    # tool-loop / synthesis style call
    trace.record_llm_call("root", "kimi", input_tokens=100, output_tokens=20, cost_usd=0.004)
    # agent-step style call
    trace.add_step(
        AgentStep(
            agent_id="root",
            model="kimi",
            input_tokens=50,
            output_tokens=10,
            total_tokens=60,
            cost_usd=0.002,
        )
    )
    assert trace.total_cost_usd == 0.006
    assert trace.total_input_tokens == 150
    assert trace.token_by_model["kimi"]["cost"] == 0.006
    assert trace.token_by_model["kimi"]["input"] == 150


def test_record_run_usage_writes_both_sinks(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    store.create_job("j1", "single-agent-design", "hi")

    result = RunResult(
        output="done",
        usage=UsageSummary(
            input_tokens=1800,
            output_tokens=320,
            total_tokens=2120,
            cost_usd=0.0421,
            by_model={"kimi": {"input": 1800, "output": 320, "total": 2120, "cost": 0.0421}},
        ),
    )
    _record_run_usage(store, "j1", result)

    # sink 1: run_usage (feeds /usage + /usage/{job_id})
    summ = store.get_usage_summary(job_id="j1")
    assert summ["total_input_tokens"] == 1800 and summ["total_output_tokens"] == 320
    assert summ["total_cost_usd"] == 0.0421
    by_model = store.get_usage_by_model()
    assert by_model[0]["model"] == "kimi" and by_model[0]["cost_usd"] == 0.0421

    # sink 2: jobs table usage columns (feeds /jobs/history, which the panel federates)
    row = next(j for j in store.list_jobs(limit=10) if j.id == "j1")
    assert row.usage_input_tokens == 1800
    assert row.usage_output_tokens == 320
    assert row.usage_cost_usd == 0.0421


def test_record_run_usage_is_a_noop_without_usage(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    store.create_job("j1", "t", "hi")
    _record_run_usage(store, "j1", RunResult(output="x", usage=None))
    assert store.get_usage_summary(job_id="j1")["total_input_tokens"] == 0
