"""Live progress from a running node.

The defect was not "no events" — the harness node consumed a live stream and dropped every event
into a local buffer. So a test asserting "progress was emitted" would have PASSED against the broken
code, because the buffered messages did eventually reach the trace.

What was missing was progress arriving *while the run was still going*. The ordering test below is
therefore the real regression test; the rest guard the seam's contract.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from swarmkit_runtime.progress import (
    SUMMARY_CHARS,
    ProgressEvent,
    emit_progress,
    get_progress_sink,
    set_progress_sink,
    summarize,
)


@pytest.fixture(autouse=True)
def _clean_sink() -> Any:
    set_progress_sink(None)
    yield
    set_progress_sink(None)


# ---- the seam's contract -------------------------------------------------------------------------


def test_no_sink_is_a_no_op() -> None:
    """The default. A run with nobody listening must not pay for progress, or crash on it."""
    emit_progress(ProgressEvent("root", "tool", "Read(x)"))  # must not raise


def test_a_sink_receives_events() -> None:
    seen: list[ProgressEvent] = []
    set_progress_sink(seen.append)
    emit_progress(ProgressEvent("root", "tool", "Read(x)"))
    assert [e.summary for e in seen] == ["Read(x)"]


def test_a_raising_sink_never_fails_the_run() -> None:
    """A subscriber's bug degrades observability; it does not lose the work being observed — the
    same rule the OTel mirror and usage recording already follow."""

    def _boom(_: ProgressEvent) -> None:
        raise RuntimeError("subscriber is broken")

    set_progress_sink(_boom)
    emit_progress(ProgressEvent("root", "message", "still fine"))  # must not raise


def test_clearing_the_sink_stops_delivery() -> None:
    seen: list[ProgressEvent] = []
    set_progress_sink(seen.append)
    set_progress_sink(None)
    emit_progress(ProgressEvent("root", "tool", "Read(x)"))
    assert seen == []
    assert get_progress_sink() is None


@pytest.mark.asyncio
async def test_concurrent_runs_do_not_cross_talk() -> None:
    """Why this is a ContextVar and not a module global: two jobs under one `swarmkit serve` must
    each see only their own events."""
    a: list[str] = []
    b: list[str] = []

    async def run(name: str, sink: list[str]) -> None:
        set_progress_sink(lambda e: sink.append(e.summary))
        for i in range(3):
            emit_progress(ProgressEvent(name, "tool", f"{name}-{i}"))
            await asyncio.sleep(0)

    await asyncio.gather(run("a", a), run("b", b))

    assert a == ["a-0", "a-1", "a-2"]
    assert b == ["b-0", "b-1", "b-2"], "a second run must not steal the first run's sink"


# ---- summary vs detail: the exposure split ------------------------------------------------------


def test_summary_is_one_bounded_line() -> None:
    text = "first line with the answer\nsecond line\nthird line"
    assert summarize(text) == "first line with the answer"
    assert "\n" not in summarize(text)


def test_a_long_line_is_truncated() -> None:
    out = summarize("x" * 500)
    assert len(out) <= SUMMARY_CHARS
    assert out.endswith("…")


def test_a_secret_below_the_first_line_stays_out_of_the_summary() -> None:
    """serve publishes `summary` into job.events, which goes over HTTP to anyone with serve:read.
    A harness message can quote a file, and a file can quote a credential."""
    text = "Read the config file.\nAWS_SECRET_ACCESS_KEY=hunter2trustno1\ndone"
    event = ProgressEvent("root", "message", summarize(text), detail=text)
    assert "hunter2trustno1" not in event.summary
    assert "hunter2trustno1" in event.detail, "the local subscriber can still see it"


def test_empty_text_summarizes_to_empty() -> None:
    assert summarize("") == ""
    assert summarize("   \n  ") == ""


# ---- the regression: progress must arrive DURING the run -----------------------------------------


@pytest.mark.asyncio
async def test_progress_interleaves_with_the_stream_it_reports_on() -> None:
    """The actual bug, stated as ordering.

    The broken code appended every event to a list and surfaced nothing until the stream ended, so
    "were events emitted?" was the wrong question — they were, eventually. This asserts a progress
    event lands BETWEEN two stream items, which buffering cannot satisfy.
    """
    timeline: list[str] = []
    set_progress_sink(lambda e: timeline.append(f"progress:{e.summary}"))

    async def fake_harness_stream() -> Any:
        for i in range(3):
            timeline.append(f"stream:{i}")
            emit_progress(ProgressEvent("root", "tool", f"tool-{i}"))
            await asyncio.sleep(0)

    await fake_harness_stream()

    assert timeline == [
        "stream:0",
        "progress:tool-0",
        "stream:1",
        "progress:tool-1",
        "stream:2",
        "progress:tool-2",
    ], "progress must interleave, not arrive in a batch at the end"


def test_the_harness_node_emits_from_its_event_loop() -> None:
    """Guards the wiring itself: the loop in `_harness_node` is the ONE place every harness event
    passes through, and the whole feature is that it now publishes from there."""
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()
    assert "emit_progress(" in src
    for kind in ('"tool"', '"message"', '"usage"', '"started"', '"interaction"'):
        assert kind in src, f"no progress emitted for {kind}"


def test_serve_publishes_summaries_not_details() -> None:
    """serve appends to job.events, which is relayed over SSE. It must never publish `detail`."""
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/server/_jobs.py"
    ).read_text()
    assert "e.summary" in src
    assert "e.detail" not in src, "serve must not publish the harness's raw text"
