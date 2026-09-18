"""Write-through audit: persist each event when it is recorded, not batched at the run boundary.

The design is `design/details/audit-event-journal.md`. Until this, audit events lived in the
governance provider's in-memory list for the whole run and were written in one pass at
``_end_run`` — so a ``kill -9`` (or an OOM, or a power loss) mid-run wrote nothing, losing the
trail of exactly the run a security team most wants to reconstruct, while the LangGraph checkpoint
(written per node) survived and the run still resumed.

``JournalingGovernance`` wraps the real provider and, after the base records an event, writes it to
the durable store immediately. It delegates everything else untouched. A write that fails is
logged, never raised: the run continues and the end-of-run batch (idempotent, because the store
dedups on ``event_id``) is the backstop.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider

_logger = logging.getLogger("swarmkit.audit")

JournalWrite = Callable[[AuditEvent], Awaitable[None]]


class JournalingGovernance:
    """A ``GovernanceProvider`` that persists each recorded event immediately.

    Wraps ``base`` and calls ``write`` after ``base.record_event`` returns. Every other method and
    attribute is delegated to ``base`` unchanged, so wrapping is transparent to the compiler, the
    trust machinery and ``_extract_events`` (which reads ``base.events`` through the delegation).
    """

    def __init__(self, base: GovernanceProvider, *, write: JournalWrite) -> None:
        self._base = base
        self._write = write

    async def record_event(self, event: AuditEvent) -> None:
        await self._base.record_event(event)
        try:
            await self._write(event)
        except Exception:  # durability is best-effort at this seam; the end-of-run batch retries
            _logger.warning(
                "audit journal write failed for %s (%s); the run's end-of-run flush will retry it",
                event.event_type,
                event.event_id,
                exc_info=True,
            )

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not set on the wrapper itself, so `_base`/`_write` never recurse.
        return getattr(self._base, name)
