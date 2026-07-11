"""Budget envelope + liveness enforcement (executor-abstraction §6.1, P2 PR4)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    ExecEvent,
    ExecMessage,
    ExecResult,
    ExecUsage,
    enforce_budget,
)


async def _stream(*events: ExecEvent) -> AsyncIterator[ExecEvent]:
    for e in events:
        yield e


async def _drain(agen: AsyncIterator[ExecEvent]) -> list[ExecEvent]:
    return [e async for e in agen]


class _Clock:
    """Deterministic monotonic clock: returns each queued value in order, then holds the last."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self._i = 0

    def __call__(self) -> float:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


@pytest.mark.asyncio
async def test_stream_passes_through_when_under_budget() -> None:
    src = _stream(
        ExecMessage(role="assistant", text="hi"),
        ExecResult(status="success", output="done"),
    )
    out = await _drain(enforce_budget(src, BudgetEnvelope(max_turns=5)))
    assert [type(e).__name__ for e in out] == ["ExecMessage", "ExecResult"]
    assert isinstance(out[-1], ExecResult) and out[-1].status == "success"


@pytest.mark.asyncio
async def test_cost_breach_cancels_and_appends_budget_exceeded() -> None:
    cancelled = False

    async def cancel() -> None:
        nonlocal cancelled
        cancelled = True

    src = _stream(ExecUsage(cost_usd=0.6))
    out = await _drain(enforce_budget(src, BudgetEnvelope(max_cost_usd=0.5), cancel=cancel))

    assert isinstance(out[0], ExecUsage)  # the triggering event still flows through
    assert isinstance(out[-1], ExecResult)
    assert out[-1].status == "budget_exceeded"
    assert out[-1].exit_metadata["reason"] == "max_cost_usd"
    assert cancelled is True


@pytest.mark.asyncio
async def test_turn_cap_breach() -> None:
    src = _stream(
        ExecMessage(role="assistant", text="1"),
        ExecMessage(role="assistant", text="2"),
        ExecMessage(role="assistant", text="3"),
    )
    out = await _drain(enforce_budget(src, BudgetEnvelope(max_turns=2)))
    # two assistant turns flow through, then enforcement stops before the third.
    assert sum(isinstance(e, ExecMessage) for e in out) == 2
    assert isinstance(out[-1], ExecResult) and out[-1].status == "budget_exceeded"
    assert out[-1].exit_metadata["reason"] == "max_turns"


@pytest.mark.asyncio
async def test_wall_clock_breach_uses_injected_clock() -> None:
    # start=0.0, then now=120.0 on the first event → 120s >= 60s (1 minute).
    clock = _Clock(0.0, 120.0)
    src = _stream(ExecMessage(role="assistant", text="slow"))
    out = await _drain(enforce_budget(src, BudgetEnvelope(max_wall_clock_minutes=1.0), clock=clock))
    assert isinstance(out[-1], ExecResult) and out[-1].status == "budget_exceeded"
    assert out[-1].exit_metadata["reason"] == "max_wall_clock_minutes"


@pytest.mark.asyncio
async def test_idle_timeout_stalls_and_cancels() -> None:
    cancelled = False

    async def cancel() -> None:
        nonlocal cancelled
        cancelled = True

    async def _slow() -> AsyncIterator[ExecEvent]:
        await asyncio.sleep(0.2)  # longer than the idle window
        yield ExecMessage(role="assistant", text="too late")

    out = await _drain(
        enforce_budget(_slow(), BudgetEnvelope(max_idle_seconds=0.05), cancel=cancel)
    )
    assert len(out) == 1
    assert isinstance(out[0], ExecResult) and out[0].status == "stalled"
    assert out[0].exit_metadata["reason"] == "idle_timeout"
    assert cancelled is True
