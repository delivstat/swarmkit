"""AuditProvider abstraction — pluggable audit event storage.

The runtime writes audit events through GovernanceProvider.record_event(),
which delegates to the configured AuditProvider. This module defines the
interface and a registry for built-in + plugin providers.

See design/details/human-interaction-model.md and
design/details/opentelemetry-observability.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from swarmkit_runtime.governance import AuditEvent


class AuditProvider(ABC):
    """Pluggable audit event storage backend.

    All implementations must be append-only — no update or delete path
    is exposed (design §8.3). Implementations must be safe for concurrent
    writes from a single process.
    """

    provider_id: str = "abstract"

    @abstractmethod
    async def record(self, event: AuditEvent) -> None:
        """Append an event. Must not raise on duplicate event_id."""

    @abstractmethod
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
        """Query events with optional filters. Returns newest-first."""
        yield  # type: ignore[misc]

    @abstractmethod
    async def count(
        self,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        event_type: str | None = None,
    ) -> int:
        """Count events matching filters."""

    async def close(self) -> None:  # noqa: B027
        """Release resources. Called on runtime shutdown."""


class AuditProviderRegistry:
    """Registry for audit provider implementations."""

    def __init__(self) -> None:
        self._providers: dict[str, type[AuditProvider]] = {}

    def register(self, provider_id: str, cls: type[AuditProvider]) -> None:
        self._providers[provider_id] = cls

    def get(self, provider_id: str) -> type[AuditProvider] | None:
        return self._providers.get(provider_id)

    def available(self) -> list[str]:
        return sorted(self._providers.keys())


_registry = AuditProviderRegistry()


def get_registry() -> AuditProviderRegistry:
    return _registry
