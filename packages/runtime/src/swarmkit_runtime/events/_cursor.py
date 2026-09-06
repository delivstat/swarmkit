"""An opaque, resumable position in the event log.

The audit table has no monotonic integer and cannot portably gain one: SQLite cannot add an
AUTOINCREMENT column by ``ALTER TABLE``, and the store's migration facility is deliberately
additive-nullable only. Adding a column to an append-only audit table to serve a *read-side*
feature would be the wrong trade anyway.

But the ordering already exists. ``(timestamp, event_id)`` is total — the timestamp orders, the id
breaks ties — and both columns are already indexed. So the cursor is that pair, encoded as one
opaque string so callers cannot come to depend on its shape.

**Opaque matters.** A caller that parses the cursor is a caller that breaks when it becomes a
sequence number, which is exactly the migration this defers rather than forecloses.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass


@dataclass(frozen=True)
class Cursor:
    """A position in the log: everything at or before `(timestamp, event_id)` has been seen."""

    timestamp: str
    event_id: str


class CursorError(ValueError):
    """A cursor that did not come from this API."""


def encode_cursor(timestamp: str, event_id: str) -> str:
    raw = f"{timestamp}\x1f{event_id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(value: str) -> Cursor:
    """Decode a cursor, or say plainly that it is not one.

    A malformed cursor must not silently become "from the beginning": an application that
    replayed its whole history because of a typo would flood whatever it forwards to.
    """
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        timestamp, event_id = raw.split("\x1f", 1)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        msg = (
            "cursor is not one this API issued. Pass the `next_cursor` from a previous response, "
            "or omit it to start from the beginning."
        )
        raise CursorError(msg) from exc
    return Cursor(timestamp=timestamp, event_id=event_id)
