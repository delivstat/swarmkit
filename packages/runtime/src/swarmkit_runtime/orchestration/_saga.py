"""Generic saga state + the persistence Protocol for the bundled reference orchestrator
(design/details/bundled-pipeline-orchestrator.md).

This is the *domain-neutral* saga engine's state — no SDLC vocabulary (no contract locks, no source
reconciliation; those stay in the example's richer controller). One `SagaState` per correlation_id:
which stages have passed, which is in flight, whether it's parked on a gate, per-stage attempts, and
the append-only timeline. A `SagaStore` Protocol is the persistence seam; `InMemorySagaStore` is the
test double, `SqlSagaStore` (``_store.py``) the durable default.

Boundary: this package is imported only by the ``swarmkit orchestrator`` command — never by the
runtime core or serve (enforced by an import-linter contract).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

SagaStatus = Literal["active", "parked", "completed", "rejected", "failed"]


def now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class TimelineEntry:
    """One correlated transition in a run's saga timeline (the audit/replay view)."""

    seq: int
    at: str  # ISO timestamp
    stage_id: str | None
    kind: str  # started | completed | parked | resumed | rejected | failed | new
    detail: str = ""


@dataclass
class SagaState:
    """Everything the reference controller persists for one ``correlation_id``."""

    correlation_id: str
    graph_id: str = ""
    status: SagaStatus = "active"
    current_stage: str | None = None  # the stage a bounded run is in flight for
    passed_stages: list[str] = field(default_factory=list)
    pending_gate_stage: str | None = None  # the stage whose funnel we're parked on
    #: per-stage output artifact reference (correlation_id::stage), threaded to downstream stages.
    artifacts: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    tag: str = ""  # opaque caller tag (e.g. a requirement id / instance) for the searchable list
    created_at: str = ""
    updated_at: str = ""
    timeline: list[TimelineEntry] = field(default_factory=list)

    def add(self, kind: str, *, stage_id: str | None = None, detail: str = "") -> None:
        self.timeline.append(
            TimelineEntry(
                seq=len(self.timeline),
                at=now().isoformat(),
                stage_id=stage_id,
                kind=kind,
                detail=detail,
            )
        )
        self.updated_at = now().isoformat()


class SagaStore(Protocol):
    """The persistence seam: durable saga state + the event queue that decouples serve from the
    orchestrator. ``InMemorySagaStore`` for tests; ``SqlSagaStore`` for the durable default."""

    def get(self, correlation_id: str) -> SagaState | None: ...
    def create(self, correlation_id: str, *, graph_id: str, tag: str = "") -> SagaState: ...
    def save(self, saga: SagaState) -> None: ...
    def list(
        self, *, status: str = "all", graph: str | None = None, query: str = "", limit: int = 100
    ) -> list[SagaState]: ...
    # dedup of inbound events
    def seen(self, key: tuple[str, str, str]) -> bool: ...
    def mark_seen(self, key: tuple[str, str, str]) -> None: ...
    # the durable event queue (serve enqueues, the orchestrator claims)
    def enqueue(self, correlation_id: str, event: str) -> int: ...
    def claim(self, worker: str) -> tuple[int, str, str] | None: ...
    def ack(self, event_id: int) -> None: ...


class InMemorySagaStore:
    """A dict-backed :class:`SagaStore` for tests + the drive-loop oracle."""

    def __init__(self) -> None:
        self._sagas: dict[str, SagaState] = {}
        self._seen: set[tuple[str, str, str]] = set()
        self._events: list[dict[str, Any]] = []
        self._seq = 0

    def get(self, correlation_id: str) -> SagaState | None:
        return self._sagas.get(correlation_id)

    def create(self, correlation_id: str, *, graph_id: str, tag: str = "") -> SagaState:
        saga = SagaState(
            correlation_id=correlation_id,
            graph_id=graph_id,
            tag=tag,
            created_at=now().isoformat(),
            updated_at=now().isoformat(),
        )
        self._sagas[correlation_id] = saga
        return saga

    def save(self, saga: SagaState) -> None:
        self._sagas[saga.correlation_id] = saga

    def list(
        self, *, status: str = "all", graph: str | None = None, query: str = "", limit: int = 100
    ) -> list[SagaState]:
        active = {"active", "parked"}
        out = []
        for s in self._sagas.values():
            if status == "active" and s.status not in active:
                continue
            if status == "completed" and s.status in active:
                continue
            if graph and s.graph_id != graph:
                continue
            if query and query.lower() not in f"{s.correlation_id} {s.tag}".lower():
                continue
            out.append(s)
        out.sort(key=lambda s: s.updated_at, reverse=True)
        return out[:limit]

    def seen(self, key: tuple[str, str, str]) -> bool:
        return key in self._seen

    def mark_seen(self, key: tuple[str, str, str]) -> None:
        self._seen.add(key)

    def enqueue(self, correlation_id: str, event: str) -> int:
        self._seq += 1
        self._events.append(
            {"id": self._seq, "correlation_id": correlation_id, "event": event, "status": "queued"}
        )
        return self._seq

    def claim(self, worker: str) -> tuple[int, str, str] | None:
        for e in self._events:
            if e["status"] == "queued":
                e["status"] = "claimed"
                e["claimed_by"] = worker
                return (e["id"], e["correlation_id"], e["event"])
        return None

    def ack(self, event_id: int) -> None:
        for e in self._events:
            if e["id"] == event_id:
                e["status"] = "done"


__all__ = [
    "InMemorySagaStore",
    "SagaState",
    "SagaStatus",
    "SagaStore",
    "TimelineEntry",
    "now",
]
