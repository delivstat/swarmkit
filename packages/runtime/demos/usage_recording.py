"""Demo: usage recording + OpenRouter cost capture (design: runtime/usage-recording-and-cost).

Before this change the runtime captured token usage into the in-memory trace but never wrote it to
the store, so `/usage` and `/jobs/history` always reported zero. This shows both halves now wired,
using the real code paths (no live model call, no API budget):

  1. The OpenAI-compat provider reads OpenRouter's per-call cost off `raw.usage.cost`.
  2. `_record_run_usage` persists a completed run into both sinks — `run_usage` (feeds `/usage`) and
     the jobs table (feeds `/jobs/history`, which the fleet panel federates for per-run cost).

Run it:

    uv run python packages/runtime/demos/usage_recording.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from swarmkit_runtime._workspace_runtime import RunResult, UsageSummary
from swarmkit_runtime.model_providers._openai import _from_openai_response
from swarmkit_runtime.model_providers._pricing import price_per_million
from swarmkit_runtime.persistence import SqliteStore
from swarmkit_runtime.server._jobs import _record_run_usage


def _bar(label: str) -> None:
    print(f"\n--- {label}")


def main() -> None:
    _bar("1. Provider captures OpenRouter's per-call cost (raw.usage.cost -> Usage.cost_usd)")
    raw = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="a design principle", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1800, completion_tokens=320, cost=0.0421),
    )
    usage = _from_openai_response(raw).usage
    print(f"   in={usage.input_tokens} out={usage.output_tokens} cost=${usage.cost_usd}")

    _bar("2. A completed run's usage is recorded into the store")
    store = SqliteStore(Path(tempfile.mkdtemp()))
    store.create_job("job-1", "single-agent-design", "name one OMS design principle")
    print(f"   /usage before the run: {store.get_usage_summary()}")

    result = RunResult(
        output="Idempotency.",
        usage=UsageSummary(
            input_tokens=1800,
            output_tokens=320,
            total_tokens=2120,
            cost_usd=0.0421,
            by_model={
                "moonshotai/kimi-k2": {"input": 1800, "output": 320, "total": 2120, "cost": 0.0421}
            },
        ),
    )
    _record_run_usage(store, "job-1", result)

    _bar("3. Both sinks now report real tokens + cost")
    print(f"   /usage (run_usage):      {store.get_usage_summary()}")
    print(f"   /usage by model:         {store.get_usage_by_model()}")
    job = next(j for j in store.list_jobs(limit=10) if j.id == "job-1")
    print(
        f"   /jobs/history (job row): in={job.usage_input_tokens} "
        f"out={job.usage_output_tokens} cost=${job.usage_cost_usd}"
    )
    print(
        "\nReal cost now flows: provider -> trace -> store -> /usage + /jobs/history -> panel Runs."
    )

    _bar("4. Price table (PR 2): a token-only provider gets cost derived from tokens")
    model, tin, tout = "claude-sonnet-4", 10_000, 2_000
    store2 = SqliteStore(Path(tempfile.mkdtemp()))
    store2.create_job("job-2", "review", "check this")
    _record_run_usage(
        store2,
        "job-2",
        RunResult(
            output="ok",
            usage=UsageSummary(
                input_tokens=tin,
                output_tokens=tout,
                total_tokens=tin + tout,
                # Anthropic returns tokens but no cost (cost 0.0) -> priced from the table.
                by_model={model: {"input": tin, "output": tout, "total": tin + tout, "cost": 0.0}},
            ),
        ),
    )
    # Everything below is derived — the price is read from the table, the cost from the store, so
    # this narration can't drift from the actual pricing the way a hardcoded string would.
    in_per_m, out_per_m = price_per_million(model) or (0.0, 0.0)
    recorded = store2.get_usage_by_model()[0]["cost_usd"]
    print(f"   {model}: {tin} in / {tout} out")
    print(f"   table price: ${in_per_m}/${out_per_m} per 1M (input/output)")
    print(
        f"   derived cost = {tin}/1M*${in_per_m} + {tout}/1M*${out_per_m} = ${recorded} "
        f"(OpenRouter's own cost in step 3 is left untouched)"
    )


if __name__ == "__main__":
    main()
