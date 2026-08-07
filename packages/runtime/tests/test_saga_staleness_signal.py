"""`pipeline status` says when a run has stopped moving.

A saga has no timeout, so "active with a frozen `updated_at`" is the only evidence that a run has
stalled — and it was visible nowhere. The reported outage looked exactly like a healthy in-progress
run for over an hour: saga `active`, a plausible `current_stage`, no gate to resolve, nothing in the
review queue. Every individual record was the record of a success, and the view an operator reaches
for showed nothing unusual.

The signal is deliberately a question rather than a verdict. A long harness stage is
indistinguishable from a stall at this level, so the threshold is generous and it says "STALLED?" —
crying
stall over normal work teaches an operator to ignore the signal, which is worse than not having one.

Parked runs are excluded outright: a gate waits on a human, so days of no movement is correct
behaviour there and flagging it would be pure noise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from swarmkit_runtime.cli._cmd_pipeline import (
    _STALE_AFTER_SECONDS,
    _humanise,
    _stale_for,
    _summary,
)
from swarmkit_runtime.orchestration import SagaState


def _saga(
    *,
    status: Any = "active",
    gate: str | None = None,
    minutes_ago: float = 0,
    current: str | None = "triage",
) -> SagaState:
    saga = SagaState(correlation_id="WMS-17", graph_id="wms", input="the ticket")
    saga.status = status
    saga.current_stage = current
    saga.pending_gate_stage = gate
    saga.updated_at = (datetime.now(tz=UTC) - timedelta(minutes=minutes_ago)).isoformat()
    return saga


def _stalled(saga: SagaState) -> bool:
    idle = _stale_for(saga)
    return idle is not None and idle >= _STALE_AFTER_SECONDS


# ---- the reported state is caught ---------------------------------------------------------------


def test_a_run_frozen_for_an_hour_is_flagged() -> None:
    """The reported outage: active, no gate, `updated_at` frozen at creation, and nothing anywhere
    said so."""
    assert _stalled(_saga(minutes_ago=65))


def test_the_idle_time_is_reported() -> None:
    """How long it has been still is the number an operator judges by."""
    idle = _stale_for(_saga(minutes_ago=65))

    assert idle is not None
    assert 3800 < idle < 4000


# ---- normal work is not called a stall ----------------------------------------------------------


def test_a_stage_that_started_minutes_ago_is_quiet() -> None:
    """A harness stage legitimately runs for many minutes. Flagging it would train an operator to
    ignore the signal, which is worse than having none."""
    assert not _stalled(_saga(minutes_ago=4))


def test_the_threshold_is_generous() -> None:
    assert _STALE_AFTER_SECONDS >= 10 * 60


def test_a_run_parked_on_a_gate_is_never_flagged() -> None:
    """A gate waits on a human — days of no movement is the correct behaviour, not a stall."""
    assert _stale_for(_saga(gate="triage", minutes_ago=4320)) is None


def test_a_finished_run_is_never_flagged() -> None:
    assert _stale_for(_saga(status="completed", minutes_ago=600)) is None
    assert _stale_for(_saga(status="failed", minutes_ago=600)) is None


def test_an_unparseable_timestamp_does_not_raise() -> None:
    """A malformed stamp must not break `status` — the command's job is to report, and a crash
    there loses the dead-letter and timeline output too."""
    saga = _saga(minutes_ago=65)
    saga.updated_at = "not a date"

    assert _stale_for(saga) is None


def test_a_naive_timestamp_is_read_as_utc() -> None:
    """Older rows were written without an offset. Comparing naive to aware raises, which would take
    the whole command down on exactly the runs most likely to be stuck."""
    saga = _saga(minutes_ago=65)
    saga.updated_at = (
        (datetime.now(tz=UTC) - timedelta(minutes=65)).replace(tzinfo=None).isoformat()
    )

    idle = _stale_for(saga)

    assert idle is not None
    assert idle > _STALE_AFTER_SECONDS


# ---- it is machine-readable too -----------------------------------------------------------------


def test_the_json_summary_carries_the_idle_time() -> None:
    """So a fleet view or a check script can act on it rather than parsing the human line."""
    assert _summary(_saga(minutes_ago=65))["stale_for_seconds"] is not None


def test_the_json_summary_is_null_for_a_run_that_cannot_stall() -> None:
    """Null, not zero: a parked or finished run has no idle time to report, and zero would read as
    "just moved"."""
    assert _summary(_saga(gate="triage", minutes_ago=4320))["stale_for_seconds"] is None


# ---- the duration reads as a duration -----------------------------------------------------------


def test_durations_are_human_readable() -> None:
    assert _humanise(45) == "45s"
    assert _humanise(600) == "10m"
    assert _humanise(7200) == "2.0h"
