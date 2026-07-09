"""Per-model price table (PR 2) + its use in the usage write-path.

Providers that return only tokens (Anthropic/OpenAI/Google) get cost derived from the table;
OpenRouter's provider-returned cost (PR 1) is preserved, not overwritten.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit_runtime._workspace_runtime import RunResult, UsageSummary
from swarmkit_runtime.model_providers._pricing import estimate_cost, price_per_million
from swarmkit_runtime.persistence import SqliteStore
from swarmkit_runtime.server._jobs import _record_run_usage


def test_price_lookup_strips_provider_prefix_and_variant_suffix() -> None:
    # both native and OpenRouter-routed ids resolve, and dated/variant suffixes still match.
    assert price_per_million("claude-sonnet-4") == (3.0, 15.0)
    assert price_per_million("anthropic/claude-sonnet-4") == (3.0, 15.0)
    assert price_per_million("claude-3-5-sonnet-20241022") == (3.0, 15.0)
    assert price_per_million("gpt-4o-2024-08-06") == (2.50, 10.0)


def test_longest_key_wins_so_mini_is_not_billed_as_full() -> None:
    # gpt-4o-mini must match its own (cheaper) row, not the generic gpt-4o.
    assert price_per_million("gpt-4o-mini") == (0.15, 0.60)
    assert price_per_million("gpt-4o") == (2.50, 10.0)


def test_unknown_model_is_unpriced_not_guessed() -> None:
    assert price_per_million("some-local-llama") is None
    assert estimate_cost("some-local-llama", 1000, 1000) == 0.0
    assert estimate_cost("ollama/qwen2.5", 5000, 5000) == 0.0  # local → $0


def test_estimate_cost_is_linear_in_tokens() -> None:
    # 1M input + 1M output of claude-sonnet-4 = $3 + $15 = $18.
    assert estimate_cost("claude-sonnet-4", 1_000_000, 1_000_000) == 18.0
    # 1000 in / 500 out of gpt-4o-mini = 0.00015 + 0.0003 = 0.00045
    assert round(estimate_cost("gpt-4o-mini", 1000, 500), 6) == 0.00045


def test_record_fills_cost_for_token_only_provider(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    store.create_job("j1", "t", "hi")
    # A run whose provider reported tokens but no cost (cost 0) — the table fills it.
    result = RunResult(
        output="x",
        usage=UsageSummary(
            input_tokens=10_000,
            output_tokens=2_000,
            total_tokens=12_000,
            by_model={
                "claude-sonnet-4": {"input": 10_000, "output": 2_000, "total": 12_000, "cost": 0.0}
            },
        ),
    )
    _record_run_usage(store, "j1", result)
    # 10k in * $3/M + 2k out * $15/M = 0.03 + 0.03 = 0.06
    assert store.get_usage_summary(job_id="j1")["total_cost_usd"] == 0.06


def test_record_preserves_provider_reported_cost(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    store.create_job("j1", "t", "hi")
    # OpenRouter already reported real cost — the table must NOT overwrite it (even though the
    # model is also in the table via its bare name would differ; provider cost is authoritative).
    result = RunResult(
        output="x",
        usage=UsageSummary(
            input_tokens=4321,
            output_tokens=487,
            total_tokens=4808,
            cost_usd=0.00451,
            by_model={
                "moonshotai/kimi-k2.6": {
                    "input": 4321,
                    "output": 487,
                    "total": 4808,
                    "cost": 0.00451,
                }
            },
        ),
    )
    _record_run_usage(store, "j1", result)
    assert store.get_usage_summary(job_id="j1")["total_cost_usd"] == 0.00451
