"""Serve access audit, written in batches off the request path (issue 1084).

Every authenticated mutating call (``serve:run`` / ``serve:admin``) appends a ``serve_access`` row:
who called what, and the status it got. That used to be one synchronous transaction inside the
auth middleware, per request, on the event loop — on Postgres a pool checkout, a pre-ping and a
BEGIN/INSERT/COMMIT that every other request on the loop waited behind, the 429 path included.

:class:`AccessAuditWriter` keeps every row — it is an audit trail, nothing is coalesced — but
buffers them in memory and writes each batch in one transaction from a worker thread, every
:data:`FLUSH_INTERVAL_S`. Each row is stamped when the call is recorded, not when it is written.

The trade is the batch window: a process killed with ``kill -9`` loses at most the rows recorded
since the last flush. A clean shutdown flushes. The write was already best-effort — a failing store
never broke the request — so this narrows nothing a caller could rely on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: How often buffered access rows are written.
FLUSH_INTERVAL_S = 0.25
#: Rows held while the store is unreachable before new ones are dropped (and counted in the log).
MAX_BUFFERED = 10_000


class AccessAuditWriter:
    """Buffers serve access-audit rows and writes them in batches from a worker thread."""

    def __init__(self, store: Any, *, flush_interval_s: float = FLUSH_INTERVAL_S) -> None:
        self._store = store
        self._interval = flush_interval_s
        self._buffer: list[dict[str, Any]] = []
        self._dropped = 0
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def record(
        self,
        *,
        client_id: str,
        provider: str,
        method: str,
        path: str,
        action: str | None,
        status: int,
    ) -> None:
        """Queue one row. No I/O — safe on the request path."""
        if len(self._buffer) >= MAX_BUFFERED:
            self._dropped += 1
            return
        self._buffer.append(
            {
                "client_id": client_id,
                "provider": provider,
                "method": method,
                "path": path,
                "action": action,
                "status": status,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    async def flush(self) -> None:
        """Write everything buffered so far, in one transaction, off the loop.

        A failed write keeps the rows for the next flush rather than dropping them; the buffer cap
        bounds what a store that stays down can cost.
        """
        if self._dropped:
            logger.warning(
                "serve access audit: dropped %d rows (buffer full at %d — store unreachable?)",
                self._dropped,
                MAX_BUFFERED,
            )
            self._dropped = 0
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        try:
            await asyncio.to_thread(self._store.record_access_many, batch)
        except Exception:  # audit must not break serving
            logger.debug("serve access-audit write failed; retrying next flush", exc_info=True)
            self._buffer[:0] = batch[: max(0, MAX_BUFFERED - len(self._buffer))]

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="serve-access-audit")

    async def stop(self) -> None:
        """Stop the periodic flush and write what is left.

        Signalled, not cancelled: a cancel could land while a batch is being written from its
        thread. The thread would finish the write unobserved — a failure lost silently, and the
        final flush racing it — so the loop is asked to finish its current flush and exit.
        """
        self._stopping.set()
        if self._task is not None:
            await self._task
            self._task = None
        await self.flush()

    async def _run(self) -> None:
        while not self._stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), self._interval)
            await self.flush()
