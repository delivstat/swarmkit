"""In-memory AuditProvider for tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from swarmkit_runtime.audit._provider import AuditProvider
from swarmkit_runtime.governance import AuditEvent


class MockAuditProvider(AuditProvider):
    """In-memory audit store. Events are lost on process exit.

    Used in unit tests and as the default when no audit provider is configured.
    """

    provider_id = "mock"

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    async def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    async def query(
        self,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> AsyncIterator[AuditEvent]:
        count = 0
        for event in reversed(self._events):
            if count >= limit:
                break
            if run_id and event.run_id != run_id:
                continue
            if agent_id and event.agent_id != agent_id:
                continue
            if event_type and event.event_type != event_type:
                continue
            if since and event.timestamp < since:
                continue
            if until and event.timestamp > until:
                continue
            yield event
            count += 1

    async def count(
        self,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        event_type: str | None = None,
    ) -> int:
        total = 0
        for event in self._events:
            if run_id and event.run_id != run_id:
                continue
            if agent_id and event.agent_id != agent_id:
                continue
            if event_type and event.event_type != event_type:
                continue
            total += 1
        return total
