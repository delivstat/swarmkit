"""The event seam — what happened, in a form an application can act on.

`design/details/extracting-the-channels.md`. The runtime says a run started, a gate opened, a
person resolved it. What to do about that — which channel, which human, what wording — belongs to
the application, the same way sequencing left in 1.189.0.

Two halves, deliberately unequal:

* **A durable pull.** ``GET /events?after=<cursor>`` reads the audit log, which is already
  append-only and ordered. This is the source of truth an application reconciles from.
* **A best-effort push.** An `events:` sink posts to a webhook so an application does not have to
  poll. It is a latency optimisation over the pull, not a delivery guarantee, and the contract says
  so rather than implying one the runtime is not built to keep.
"""

from swarmkit_runtime.events._cursor import Cursor, CursorError, decode_cursor, encode_cursor
from swarmkit_runtime.events._sink import (
    EventSink,
    StdoutSink,
    WebhookSink,
    build_sink,
    fan_out,
)

__all__ = [
    "Cursor",
    "CursorError",
    "EventSink",
    "StdoutSink",
    "WebhookSink",
    "build_sink",
    "decode_cursor",
    "encode_cursor",
    "fan_out",
]
