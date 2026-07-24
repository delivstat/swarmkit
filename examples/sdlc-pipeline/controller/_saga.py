"""Durable per-instance saga state + its store.

The controller owns the weeks-long state SwarmKit deliberately does *not* hold; every SwarmKit
run stays bounded (design "Per-requirement saga state"). The design says this store is
SQLite/Postgres; the reference ships an in-memory store behind a small ``SagaStore`` protocol so
the persistence seam is explicit — swap :class:`InMemorySagaStore` for a durable one and nothing
else changes. See design/details/pipeline-controller.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

SagaStatus = Literal["active", "parked", "failed", "cancelled", "done"]


@dataclass(frozen=True)
class TimelineEntry:
    """One correlated event in a pipeline instance's saga timeline (the DORA/audit view)."""

    seq: int
    at: datetime
    correlation_id: str
    stage_id: str | None
    kind: str
    detail: str


@dataclass
class SagaState:
    """Everything the controller persists for one ``correlation_id``."""

    correlation_id: str
    status: SagaStatus = "active"
    # Stages currently in-flight (a SwarmKit run kicked, not yet terminal).
    current_stages: set[str] = field(default_factory=set)
    # Stages that completed cleanly, in the order they passed (compensation unwinds in reverse).
    passed_stages: list[str] = field(default_factory=list)
    # Integration-contract lock ids this saga holds.
    held_locks: set[str] = field(default_factory=set)
    # release-event -> lock ids to free when that event is processed (design `release_locks_on`).
    lock_release_triggers: dict[str, list[str]] = field(default_factory=dict)
    # The gate this saga is parked on (if any) + the stage that owns it.
    pending_gate: str | None = None
    pending_gate_stage: str | None = None
    # A stage parked because its locks were contended, plus the payload to resume it with.
    pending_lock_stage: str | None = None
    pending_lock_payload: str = ""
    # Per-stage attempt count (idempotent retry of a failed run).
    attempts: dict[str, int] = field(default_factory=dict)
    timeline: list[TimelineEntry] = field(default_factory=list)


class SagaStore(Protocol):
    """The persistence seam. In production a SQLite/Postgres-backed implementation."""

    def get(self, correlation_id: str) -> SagaState | None: ...

    def create(self, correlation_id: str) -> SagaState: ...

    def save(self, saga: SagaState) -> None: ...

    def all_ids(self) -> list[str]: ...

    def seen(self, correlation_id: str, key: tuple[str, str, str]) -> bool: ...

    def mark_seen(self, correlation_id: str, key: tuple[str, str, str]) -> None: ...


class InMemorySagaStore:
    """A dict-backed :class:`SagaStore` for the reference + demo (persistence seam noted above)."""

    def __init__(self) -> None:
        self._sagas: dict[str, SagaState] = {}
        self._seen: set[tuple[str, str, str]] = set()

    def get(self, correlation_id: str) -> SagaState | None:
        return self._sagas.get(correlation_id)

    def create(self, correlation_id: str) -> SagaState:
        saga = SagaState(correlation_id=correlation_id)
        self._sagas[correlation_id] = saga
        return saga

    def save(self, saga: SagaState) -> None:
        # In-memory: the object is mutated in place, so this is a persistence-seam marker.
        self._sagas[saga.correlation_id] = saga

    def all_ids(self) -> list[str]:
        return list(self._sagas)

    def seen(self, correlation_id: str, key: tuple[str, str, str]) -> bool:
        return key in self._seen

    def mark_seen(self, correlation_id: str, key: tuple[str, str, str]) -> None:
        self._seen.add(key)


def now() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "InMemorySagaStore",
    "SagaState",
    "SagaStatus",
    "SagaStore",
    "TimelineEntry",
    "now",
]
