"""Has a human asked this run to stop? (design/details/stopping-a-run.md)

`swarmkit stop <run-id>` has to reach a run in **another process** — that is the whole point of the
command, since the terminal that started a run already has Ctrl-C. So the request is a durable flag
on the `jobs` row, and this module is how a running node reads it.

**A `ContextVar` holding a checker, not a store threaded through the compiler.** The compiler builds
graph nodes and has no business knowing what a database is; `WorkspaceRuntime` already knows both
the workspace root and the run id, so it installs the checker for the duration of the run. Nodes run
as tasks created inside the run's context and inherit it.

**Asked every time, not cached.** The first version cached a "no" for a second, on the reasoning
that thirty agents should not issue thirty round-trips. The demo showed what that actually buys: a
run whose agents are fast blows straight past the request and finishes, so the stop is not late — it
is *missed*. One indexed primary-key SELECT against a node that takes seconds to minutes is not a
cost worth a feature that sometimes does nothing. A seen stop still latches, because that one is
free: the run is already raising.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import Any

#: `() -> bool` for the current run, or None outside one (a test, a compile, a dry run).
_checker: ContextVar[Callable[[], bool] | None] = ContextVar("swarmkit_stop_checker", default=None)


def stop_requested() -> bool:
    """Whether a stop has been requested for the run this task belongs to.

    False outside a run, and false when the store cannot be read — a store that will not answer is
    a reason to keep running, never a reason to kill work in flight.
    """
    checker = _checker.get()
    if checker is None:
        return False
    try:
        return bool(checker())
    except Exception:
        return False


def set_stop_checker(checker: Callable[[], bool] | None) -> Token[Callable[[], bool] | None]:
    """Install the checker for this run. Pass the token to :func:`reset_stop_checker`."""
    return _checker.set(checker)


def reset_stop_checker(token: Token[Callable[[], bool] | None]) -> None:
    _checker.reset(token)


def store_backed_checker(store: Any, run_id: str) -> Callable[[], bool]:
    """A checker that reads `jobs.stop_requested_at` at every call.

    Latches on: once the flag has been seen, later calls answer True without asking again — the run
    is about to raise, and re-reading a row to confirm a decision already made buys nothing.
    """
    seen = {"stopped": False}

    def check() -> bool:
        if seen["stopped"]:
            return True
        row = store.get_job(run_id)
        seen["stopped"] = bool(row is not None and row.stop_requested_at)
        return seen["stopped"]

    return check


__all__ = [
    "reset_stop_checker",
    "set_stop_checker",
    "stop_requested",
    "store_backed_checker",
]
