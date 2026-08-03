"""Live progress from a running node (design/details/harness-progress-stream.md).

A harness node already consumes the executor's event stream, but every event terminated in a local
buffer — so a multi-minute run was silent on the CLI and on serve's SSE endpoint alike. The events
existed; nothing surfaced them, and the absence looked exactly like a hung run.

This is the seam that surfaces them. A **per-run** sink, held in a ContextVar rather than a module
global for the same reason ``_active_trace_var`` is: asyncio copies the context when a task is
created, so concurrent runs under one ``swarmkit serve`` each see their own sink instead of
clobbering each other.

Two rules that are not incidental:

- **Emission is best-effort.** A sink that raises must never fail the run — the rule already applied
  to the OTel mirror and to usage recording. Bad observability is not worth losing work over.
- **``summary`` is safe to publish, ``detail`` is not.** A harness's assistant text can carry file
  contents, and file contents are where a credential shows up. serve publishes summaries into
  ``job.events`` (readable by anyone with ``serve:read``); the local CLI may print the detail,
  because a terminal that already holds the workspace and its credentials is a different blast
  radius from a shared job record.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

logger = logging.getLogger("swarmkit.progress")

ProgressKind = Literal["started", "tool", "message", "usage", "interaction", "finished"]

#: How much of a harness message survives into the publishable summary.
SUMMARY_CHARS = 120


@dataclass(frozen=True)
class ProgressEvent:
    """One thing that happened while a node was running."""

    agent_id: str
    kind: ProgressKind
    #: Always safe to publish — never carries harness output verbatim.
    summary: str
    #: May contain the harness's own text. Local subscribers only.
    detail: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


ProgressSink = Callable[[ProgressEvent], None]

_sink_var: ContextVar[ProgressSink | None] = ContextVar("swarmkit_progress_sink", default=None)


def set_progress_sink(sink: ProgressSink | None) -> None:
    """Install (or clear) the progress sink for this execution context."""
    _sink_var.set(sink)


def get_progress_sink() -> ProgressSink | None:
    return _sink_var.get()


def emit_progress(event: ProgressEvent) -> None:
    """Publish an event to the active sink. No sink installed → no-op.

    Swallows anything the sink raises: a subscriber's bug degrades observability, it does not fail
    the run it is observing.
    """
    sink = _sink_var.get()
    if sink is None:
        return
    try:
        sink(event)
    except Exception:
        logger.debug("progress sink raised; dropping the event", exc_info=True)


def summarize(text: str, limit: int = SUMMARY_CHARS) -> str:
    """First line of *text*, bounded — what is safe to put in a shared job record."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


__all__ = [
    "SUMMARY_CHARS",
    "ProgressEvent",
    "ProgressKind",
    "ProgressSink",
    "emit_progress",
    "get_progress_sink",
    "set_progress_sink",
    "summarize",
]
