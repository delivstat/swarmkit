"""Budget envelope + liveness enforcement for harness executors (executor-abstraction §6.1, P2 PR4).

Enforcement is **core-owned**, not the adapter's: :func:`enforce_budget` wraps an executor's
:data:`ExecEvent` stream, passes events through untouched while metering ``exec.usage``, and
hard-stops on a breach — cancelling the run and appending a terminal :class:`ExecResult`:

- ``max_cost_usd`` / ``max_turns`` / ``max_wall_clock_minutes`` → ``budget_exceeded``
- ``max_idle_seconds`` (no event in the window) → ``stalled``

Terminal status is *semantic*, never an exit code — a breach is a breach regardless of how the
subprocess exits. If the wrapped stream ends on its own terminal :class:`ExecResult`, that flows
through unchanged and no synthetic result is appended.

The wall clock is injectable (``clock``) so enforcement is testable without real time; the idle
window uses :func:`asyncio.wait_for` on the next event. A "turn" is one assistant
:class:`ExecMessage` — adapters emit one per model turn.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from swarmkit_runtime.executors._events import ExecEvent, ExecMessage, ExecResult, ExecUsage
from swarmkit_runtime.executors._run import BudgetEnvelope

CancelFn = Callable[[], Awaitable[None]]


async def enforce_budget(
    events: AsyncIterator[ExecEvent],
    budget: BudgetEnvelope,
    *,
    cancel: CancelFn | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[ExecEvent]:
    """Wrap ``events`` with budget + liveness enforcement, yielding the same stream plus a terminal
    :class:`ExecResult` on breach.

    ``cancel`` (if given) is awaited before the synthetic result is yielded, so the subprocess is
    torn down before the caller sees the breach. ``clock`` returns monotonic seconds.
    """
    start = clock()
    turns = 0
    cost = 0.0
    iterator = events.__aiter__()

    async def _cancel() -> None:
        if cancel is not None:
            await cancel()

    while True:
        try:
            if budget.max_idle_seconds is not None:
                event = await asyncio.wait_for(
                    iterator.__anext__(), timeout=budget.max_idle_seconds
                )
            else:
                event = await iterator.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError:
            await _cancel()
            yield ExecResult(
                status="stalled",
                exit_metadata={
                    "reason": "idle_timeout",
                    "max_idle_seconds": budget.max_idle_seconds,
                },
            )
            return

        now = clock()
        yield event

        # The adapter produced its own terminal result — nothing left to enforce.
        if isinstance(event, ExecResult):
            return

        if isinstance(event, ExecUsage) and event.cost_usd is not None:
            cost += event.cost_usd
        elif isinstance(event, ExecMessage) and event.role == "assistant":
            turns += 1

        breach: tuple[str, float] | None = None
        if budget.max_cost_usd is not None and cost >= budget.max_cost_usd:
            breach = ("max_cost_usd", cost)
        elif budget.max_turns is not None and turns >= budget.max_turns:
            breach = ("max_turns", turns)
        elif (
            budget.max_wall_clock_minutes is not None
            and (now - start) >= budget.max_wall_clock_minutes * 60
        ):
            breach = ("max_wall_clock_minutes", now - start)

        if breach is not None:
            reason, value = breach
            await _cancel()
            yield ExecResult(
                status="budget_exceeded",
                exit_metadata={"reason": reason, "value": value},
            )
            return
