"""SqlSagaStore — the durable default for the bundled reference orchestrator
(design/details/bundled-pipeline-orchestrator.md).

A :class:`SagaStore` over SQLAlchemy Core (SQLite default, Postgres via the same seam), reusing
``persistence._store.make_engine``. Three tables: ``pipeline_saga`` (mutable current-state, one row
per correlation_id), ``pipeline_saga_seen`` (append-only event dedup), and ``pipeline_events`` — the
durable queue that decouples serve (enqueues) from the orchestrator (claims). State is written after
every transition, so a restart resumes mid-saga. In-memory is demoted to a test double.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    Table,
    Text,
    delete,
    insert,
    select,
    update,
)

from swarmkit_runtime.orchestration._saga import SagaState, SagaStatus, TimelineEntry, now
from swarmkit_runtime.persistence._store import make_engine

metadata = MetaData()

pipeline_saga = Table(
    "pipeline_saga",
    metadata,
    Column("correlation_id", Text, primary_key=True),
    Column("graph_id", Text, nullable=False, default=""),
    Column("status", Text, nullable=False, default="active"),
    Column("current_stage", Text),
    Column("passed_stages", Text, nullable=False, default="[]"),
    Column("pending_gate_stage", Text),
    Column("artifacts", Text, nullable=False, default="{}"),
    Column("attempts", Text, nullable=False, default="{}"),
    Column("tag", Text, nullable=False, default=""),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("timeline", Text, nullable=False, default="[]"),
)

pipeline_saga_seen = Table(
    "pipeline_saga_seen",
    metadata,
    Column("key", Text, primary_key=True),  # "correlation_id|event|source_event_id"
)

pipeline_events = Table(
    "pipeline_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("correlation_id", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("status", Text, nullable=False, default="queued"),  # queued | claimed | done
    Column("claimed_by", Text),
    Column("created_at", Text, nullable=False),
)


class SqlSagaStore:
    """Durable saga store + event queue. Implements the ``SagaStore`` Protocol."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        metadata.create_all(engine)

    @classmethod
    def from_url(cls, url: str) -> SqlSagaStore:
        return cls(make_engine(url))

    @property
    def engine(self) -> Engine:
        return self._engine

    # ── saga state ──────────────────────────────────────────────────────────────────────────────
    def get(self, correlation_id: str) -> SagaState | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(pipeline_saga).where(pipeline_saga.c.correlation_id == correlation_id)
                )
                .mappings()
                .first()
            )
        return _row_to_saga(dict(row)) if row else None

    def create(self, correlation_id: str, *, graph_id: str, tag: str = "") -> SagaState:
        ts = now().isoformat()
        saga = SagaState(
            correlation_id=correlation_id, graph_id=graph_id, tag=tag, created_at=ts, updated_at=ts
        )
        with self._engine.begin() as conn:
            conn.execute(insert(pipeline_saga).values(**_saga_to_row(saga)))
        return saga

    def save(self, saga: SagaState) -> None:
        saga.updated_at = now().isoformat()
        row = _saga_to_row(saga)
        with self._engine.begin() as conn:
            conn.execute(
                update(pipeline_saga)
                .where(pipeline_saga.c.correlation_id == saga.correlation_id)
                .values(**{k: v for k, v in row.items() if k != "correlation_id"})
            )

    def list(
        self, *, status: str = "all", graph: str | None = None, query: str = "", limit: int = 100
    ) -> list[SagaState]:
        stmt = select(pipeline_saga)
        if status == "active":
            stmt = stmt.where(pipeline_saga.c.status.in_(("active", "parked")))
        elif status == "completed":
            stmt = stmt.where(pipeline_saga.c.status.in_(("completed", "rejected", "failed")))
        if graph:
            stmt = stmt.where(pipeline_saga.c.graph_id == graph)
        stmt = stmt.order_by(pipeline_saga.c.updated_at.desc())
        with self._engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(stmt).mappings()]
        q = query.strip().lower()
        if q:
            rows = [r for r in rows if q in f"{r['correlation_id']} {r['tag']}".lower()]
        return [_row_to_saga(r) for r in rows[:limit]]

    # ── event dedup ─────────────────────────────────────────────────────────────────────────────
    def seen(self, key: tuple[str, str, str]) -> bool:
        k = "|".join(key)
        with self._engine.connect() as conn:
            return (
                conn.execute(
                    select(pipeline_saga_seen.c.key).where(pipeline_saga_seen.c.key == k)
                ).first()
                is not None
            )

    def mark_seen(self, key: tuple[str, str, str]) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(pipeline_saga_seen).values(key="|".join(key)))

    # ── durable event queue ─────────────────────────────────────────────────────────────────────
    def enqueue(self, correlation_id: str, event: str) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(pipeline_events).values(
                    correlation_id=correlation_id,
                    event=event,
                    status="queued",
                    created_at=now().isoformat(),
                )
            )
            pk = result.inserted_primary_key
            return int(pk[0]) if pk is not None else 0

    def claim(self, worker: str) -> tuple[int, str, str] | None:
        """Atomically claim the oldest queued event (mirrors persistence.claim_queued): pick the
        oldest, then UPDATE …WHERE status='queued' and check rowcount, retrying on a lost race."""
        while True:
            with self._engine.begin() as conn:
                row = (
                    conn.execute(
                        select(pipeline_events)
                        .where(pipeline_events.c.status == "queued")
                        .order_by(pipeline_events.c.id.asc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    return None
                result = conn.execute(
                    update(pipeline_events)
                    .where(pipeline_events.c.id == row["id"], pipeline_events.c.status == "queued")
                    .values(status="claimed", claimed_by=worker)
                )
                # psycopg reports rowcount = -1 for a matched-but-unchanged update; a real claim
                # changes status, so a nonzero rowcount = we won the race. rowcount 0 = lost.
                if result.rowcount != 0:
                    return (int(row["id"]), row["correlation_id"], row["event"])
            # lost the race — loop and try the next queued event.

    def ack(self, event_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(pipeline_events)
                .where(pipeline_events.c.id == event_id)
                .values(status="done")
            )

    def purge_done_events(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(pipeline_events).where(pipeline_events.c.status == "done"))


def _saga_to_row(s: SagaState) -> dict[str, Any]:
    return {
        "correlation_id": s.correlation_id,
        "graph_id": s.graph_id,
        "status": s.status,
        "current_stage": s.current_stage,
        "passed_stages": json.dumps(s.passed_stages),
        "pending_gate_stage": s.pending_gate_stage,
        "artifacts": json.dumps(s.artifacts),
        "attempts": json.dumps(s.attempts),
        "tag": s.tag,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "timeline": json.dumps([_tl_to_dict(t) for t in s.timeline]),
    }


def _row_to_saga(r: dict[str, Any]) -> SagaState:
    return SagaState(
        correlation_id=r["correlation_id"],
        graph_id=r["graph_id"],
        status=r["status"],
        current_stage=r["current_stage"],
        passed_stages=json.loads(r["passed_stages"]),
        pending_gate_stage=r["pending_gate_stage"],
        artifacts=json.loads(r["artifacts"]),
        attempts=json.loads(r["attempts"]),
        tag=r["tag"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        timeline=[_dict_to_tl(d) for d in json.loads(r["timeline"])],
    )


def _tl_to_dict(t: TimelineEntry) -> dict[str, Any]:
    return {"seq": t.seq, "at": t.at, "stage_id": t.stage_id, "kind": t.kind, "detail": t.detail}


def _dict_to_tl(d: dict[str, Any]) -> TimelineEntry:
    return TimelineEntry(
        seq=d["seq"],
        at=d["at"],
        stage_id=d.get("stage_id"),
        kind=d["kind"],
        detail=d.get("detail", ""),
    )


_STATUSES: tuple[SagaStatus, ...] = ("active", "parked", "completed", "rejected", "failed")

__all__ = ["SqlSagaStore", "metadata", "pipeline_events", "pipeline_saga", "pipeline_saga_seen"]
