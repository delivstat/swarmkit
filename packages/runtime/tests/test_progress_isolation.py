"""Progress listeners are scoped to the async context, not process-global — so two
concurrent `serve` conversations never cross-emit each other's progress (review
finding: CLI/server C3)."""

from __future__ import annotations

import asyncio

import pytest
from swarmkit_runtime.langgraph_compiler._helpers import _progress, progress_listener


@pytest.mark.asyncio
async def test_progress_listeners_are_context_scoped() -> None:
    a_lines: list[str] = []
    b_lines: list[str] = []

    async def run(sink: list[str], msg: str) -> None:
        with progress_listener(sink.append):
            await asyncio.sleep(0.01)  # yield so both tasks interleave
            _progress(msg)

    await asyncio.gather(run(a_lines, "for-A"), run(b_lines, "for-B"))

    # Each conversation saw only its own progress — no cross-contamination.
    assert a_lines == ["for-A"]
    assert b_lines == ["for-B"]


@pytest.mark.asyncio
async def test_progress_listener_removed_on_exit() -> None:
    seen: list[str] = []
    with progress_listener(seen.append):
        _progress("inside")
    _progress("outside")  # listener already removed
    assert seen == ["inside"]
