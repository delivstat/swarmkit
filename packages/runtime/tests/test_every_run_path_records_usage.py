"""Every way of running a topology records both usage sinks, and says where it came from.

A run's usage has two sinks. One ``run_usage`` row **per model** feeds ``/usage`` and
``/usage/{job_id}`` — the only source of the per-model cost breakdown the dashboard shows. Job-level
totals on the ``jobs`` row feed ``/jobs/history``.

The recorder that writes both lived inside ``server/_jobs.py``, reachable only from
``POST /run/{topology}``. As the other paths gained job records — CLI 1.150.0, pipeline stages
1.152.0, chat 1.155.0 — each hand-rolled the job-level half and wrote no ``run_usage`` rows. So
``/usage`` answered for one path in four and reported the rest as nothing, and a dashboard reading
it showed a workspace that had apparently stopped working.

They also read ``usage.cost_usd`` directly. Providers that report only tokens leave that at zero,
so those runs recorded as free rather than as priced from the table.
"""

from __future__ import annotations

from typing import Any, ClassVar

from swarmkit_runtime.persistence import record_run_usage, usage_fields


class _Store:
    def __init__(self) -> None:
        self.usage_rows: list[Any] = []

    def record_usage(self, row: Any) -> None:
        self.usage_rows.append(row)


class _Usage:
    """A usage summary shaped like the runtime's, reporting cost per model."""

    input_tokens = 1500
    output_tokens = 500
    cost_usd = 0.0
    by_model: ClassVar[dict[str, dict[str, Any]]] = {
        "anthropic/claude-opus-5": {"input": 1000, "output": 400, "cost": 0.03},
        "openai/gpt-5": {"input": 500, "output": 100, "cost": 0.01},
    }


class _TokenOnlyUsage:
    """A provider that reports tokens and no cost — Ollama, most local runners."""

    input_tokens = 1000
    output_tokens = 200
    cost_usd = 0.0
    by_model: ClassVar[dict[str, dict[str, Any]]] = {
        "anthropic/claude-opus-4-5": {"input": 1000, "output": 200}
    }


# ---- both sinks ------------------------------------------------------------------------------


def test_a_row_is_written_per_model() -> None:
    """The `/usage` breakdown is per model, so one row per model is the whole point."""
    store = _Store()

    record_run_usage(store, "j1", _Usage())

    assert {r.model for r in store.usage_rows} == {
        "anthropic/claude-opus-5",
        "openai/gpt-5",
    }


def test_the_rows_carry_the_job_id() -> None:
    """`/usage/{job_id}` filters on it — without it a run's cost is in the total and attributable
    to nothing."""
    store = _Store()

    record_run_usage(store, "j1", _Usage())

    assert {r.job_id for r in store.usage_rows} == {"j1"}


def test_the_job_totals_come_back_for_the_row() -> None:
    fields = usage_fields(_Usage(), "j1", _Store())

    assert fields["usage_input_tokens"] == 1500
    assert fields["usage_output_tokens"] == 500
    assert fields["usage_cost_usd"] == 0.04


# ---- cost is derived, not assumed -------------------------------------------------------------


def test_a_token_only_provider_is_priced_from_the_table() -> None:
    """`usage.cost_usd` is 0.0 here. Taking it at face value — as the three hand-rolled recorders
    did — records a real run as free, which is worse than recording nothing."""
    store = _Store()

    cost = record_run_usage(store, "j1", _TokenOnlyUsage())

    assert cost > 0.0, "a token-only run must be priced, not recorded as free"


def test_a_provider_reported_cost_wins() -> None:
    """Where the provider prices the call itself — OpenRouter — that number is authoritative and
    must not be replaced by an estimate."""
    store = _Store()

    cost = record_run_usage(store, "j1", _Usage())

    assert cost == 0.03 + 0.01


# ---- bookkeeping never costs the run ----------------------------------------------------------


def test_no_store_returns_no_fields_and_does_not_raise() -> None:
    assert usage_fields(_Usage(), "j1", None)["usage_input_tokens"] == 1500


def test_no_usage_returns_nothing() -> None:
    assert usage_fields(None, "j1", _Store()) == {}


def test_a_broken_store_does_not_raise() -> None:
    """A run that succeeded must not fail because its cost could not be filed."""

    class _Broken(_Store):
        def record_usage(self, row: Any) -> None:
            raise OSError("disk went away")

    assert record_run_usage(_Broken(), "j1", _Usage()) >= 0.0


# ---- every path uses it -----------------------------------------------------------------------


def test_no_run_path_hand_rolls_the_usage_fields() -> None:
    """Stated against the sources, because the failure mode is a NEW path quietly copying the
    job-level half again — which is exactly how `/usage` came to answer for one path in four.
    """
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"
    recorders = [
        root / "cli/_cmd_run.py",
        root / "_conversation.py",
        root / "server/_pipeline_stage.py",
        root / "server/_jobs.py",
    ]

    for path in recorders:
        body = path.read_text()
        assert "usage_fields(" in body, f"{path.name} does not use the shared recorder"
        assert 'fields["usage_input_tokens"]' not in body, (
            f"{path.name} hand-rolls the job totals again — it will write no run_usage rows"
        )


def test_every_path_names_its_source() -> None:
    """`source` is what lets the dashboard say where work comes from, and it disambiguates
    `correlation_id`, which a pipeline run and a chat conversation both use."""
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"
    expected = {
        "cli/_cmd_run.py": '"cli"',
        "_conversation.py": '"chat"',
        "server/_pipeline_stage.py": '"pipeline"',
        "server/_services.py": '"serve"',
    }

    for rel, source in expected.items():
        body = (root / rel).read_text()
        creates = [line for line in body.splitlines() if "create_job(" in line]
        assert creates, f"{rel} no longer creates a job"
        assert any(source in line for line in creates), f"{rel} does not record source={source}"
