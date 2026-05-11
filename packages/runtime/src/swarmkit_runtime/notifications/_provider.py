"""NotificationProvider abstraction and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class NotificationEvent:
    """Structured event payload sent to notification providers."""

    event_type: Literal["hitl_requested", "run_ended_error", "skill_gap_surfaced"]
    run_id: str
    topology_id: str
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


class NotificationProvider(ABC):
    """Base class for notification providers.

    Implementations must be async-safe and handle their own errors
    (a failed notification must not crash the runtime).
    """

    provider_id: str = "abstract"

    @abstractmethod
    async def notify(self, event: NotificationEvent) -> bool:
        """Send a notification. Returns True if delivered successfully."""


@dataclass
class NotificationConfig:
    """Configuration for a single notification provider instance."""

    provider: str
    config: dict[str, Any] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)


class NotificationRegistry:
    """Registry and dispatcher for notification providers.

    Holds configured provider instances and dispatches events to
    matching providers based on their event filters. Persists every
    notification to the NotificationStore for CLI/web UI access.
    """

    def __init__(self, store: Any | None = None) -> None:
        self._providers: list[tuple[NotificationProvider, list[str]]] = []
        self._store = store

    def register(self, provider: NotificationProvider, events: list[str] | None = None) -> None:
        """Register a provider with optional event filter.

        If events is None or empty, the provider receives all events.
        """
        self._providers.append((provider, events or []))

    async def dispatch(self, event: NotificationEvent) -> list[bool]:
        """Dispatch an event to all matching providers.

        Persists the notification to the store, then delivers to
        external providers. Delivery status is tracked per-provider.
        Returns a list of success/failure booleans per provider.
        """
        notif_id: str | None = None
        if self._store is not None:
            notif_id = self._store.create(event)

        results: list[bool] = []
        for provider, event_filter in self._providers:
            if event_filter and event.event_type not in event_filter:
                continue
            try:
                result = await provider.notify(event)
            except Exception as exc:
                result = False
                if self._store is not None and notif_id:
                    self._store.mark_failed(notif_id, provider.provider_id, str(exc))
            else:
                if result and self._store is not None and notif_id:
                    self._store.mark_delivered(notif_id, provider.provider_id)
                elif not result and self._store is not None and notif_id:
                    self._store.mark_failed(notif_id, provider.provider_id, "delivery failed")
            results.append(result)
        return results

    @property
    def provider_count(self) -> int:
        return len(self._providers)
