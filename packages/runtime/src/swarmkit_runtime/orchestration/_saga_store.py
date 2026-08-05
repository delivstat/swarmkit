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
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    Table,
    Text,
    and_,
    delete,
    insert,
    inspect,
    or_,
    select,
    text,
    update,
)

from swarmkit_runtime.orchestration._saga import SagaState, SagaStatus, TimelineEntry, now
from swarmkit_runtime.persistence._store import create_all_idempotent, make_engine

#: How long a claim survives without a heartbeat before another worker may take the event. The
#: handler heartbeats while it works, so this bounds only a worker that stopped heartbeating —
#: i.e. one that died — rather than one that is merely slow.
_DEFAULT_VISIBILITY_TIMEOUT = 300.0


def _now() -> str:
    return now().isoformat()


def _now_minus(seconds: float) -> str:
    return (now() - timedelta(seconds=seconds)).isoformat()


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
    Column("input", Text, nullable=False, default=""),
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
    Column("status", Text, nullable=False, default="queued"),  # queued | claimed | done | failed
    Column("claimed_by", Text),
    Column("created_at", Text, nullable=False),
    #: When the current claim was taken. A claim with no heartbeat older than the visibility
    #: timeout is reclaimable — the only thing that recovers an event from a SIGKILLed worker,
    #: which no `except` block ever sees.
    Column("claimed_at", Text),
    #: How many times this event has been handed to a worker. Incremented by `claim`, so it bounds
    #: a crash loop and a failure loop with one counter.
    Column("attempts", Integer, nullable=False, default=0),
    #: Why the last attempt failed — the difference between a dead-lettered event and a
    #: disappeared one.
    Column("last_error", Text),
)


class SqlSagaStore:
    """Durable saga store + event queue. Implements the ``SagaStore`` Protocol."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        create_all_idempotent(metadata, engine)
        self._migrate_events()

    def _migrate_events(self) -> None:
        """Add event columns introduced after the initial schema.

        ``create_all`` builds the full schema for a fresh database and does NOT alter an existing
        table, so without this an upgraded deployment fails on the next claim with "no such column".
        The same additive pattern the control-plane's stores use (``_registry._migrate``), applied
        to both dialects because Postgres is a first-class backend here.
        """
        existing = {c["name"] for c in inspect(self._engine).get_columns("pipeline_events")}
        additions = {
            "claimed_at": "TEXT",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "last_error": "TEXT",
        }
        missing = {c: ddl for c, ddl in additions.items() if c not in existing}
        if not missing:
            return
        with self._engine.begin() as conn:
            for column, ddl in missing.items():
                conn.execute(text(f"ALTER TABLE pipeline_events ADD COLUMN {column} {ddl}"))

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

    def create(
        self, correlation_id: str, *, graph_id: str, tag: str = "", input: str = ""
    ) -> SagaState:
        ts = now().isoformat()
        saga = SagaState(
            correlation_id=correlation_id,
            graph_id=graph_id,
            tag=tag,
            input=input,
            created_at=ts,
            updated_at=ts,
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

    def claim(
        self, worker: str, *, visibility_timeout: float = _DEFAULT_VISIBILITY_TIMEOUT
    ) -> tuple[int, str, str] | None:
        """Atomically claim the oldest available event: pick the oldest, then UPDATE …WHERE the
        status is still what we saw and check rowcount, retrying on a lost race.

        "Available" means queued **or** a claim that has gone stale. Without the second case an
        event claimed by a worker that then died was unreachable forever: `claim` only looked at
        `queued`, so a restarted orchestrator polled past it indefinitely while its saga sat
        `active` with a frozen `updated_at` and `pipeline status` showed a normal in-progress run.

        `attempts` is incremented here, which is what bounds a crash loop with the same counter that
        bounds a failure loop — a worker dying mid-event repeatedly eventually dead-letters it
        rather than crash-looping forever.
        """
        while True:
            stale_before = _now_minus(visibility_timeout)
            with self._engine.begin() as conn:
                row = (
                    conn.execute(
                        select(pipeline_events)
                        .where(
                            or_(
                                pipeline_events.c.status == "queued",
                                and_(
                                    pipeline_events.c.status == "claimed",
                                    pipeline_events.c.claimed_at < stale_before,
                                ),
                            )
                        )
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
                    .where(
                        pipeline_events.c.id == row["id"],
                        pipeline_events.c.status == row["status"],
                        # Guard the reclaim too: another worker may have heartbeated in between.
                        pipeline_events.c.claimed_at.is_not_distinct_from(row["claimed_at"]),
                    )
                    .values(
                        status="claimed",
                        claimed_by=worker,
                        claimed_at=_now(),
                        attempts=int(row["attempts"] or 0) + 1,
                    )
                )
                # psycopg reports rowcount = -1 for a matched-but-unchanged update; a real claim
                # changes status, so a nonzero rowcount = we won the race. rowcount 0 = lost.
                if result.rowcount != 0:
                    return (int(row["id"]), row["correlation_id"], row["event"])
            # lost the race — loop and try the next available event.

    def attempts(self, event_id: int) -> int:
        """How many times this event has been handed out. The drive loop's retry bound."""
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(pipeline_events.c.attempts).where(pipeline_events.c.id == event_id)
                )
                .mappings()
                .first()
            )
        return int(row["attempts"]) if row else 0

    def heartbeat(self, event_id: int) -> None:
        """Refresh a claim while the handler is still working.

        A stage run can legitimately outlast any visibility timeout worth setting, so the claim is
        kept alive rather than the timeout being guessed high enough to cover the slowest plausible
        stage. A guessed ceiling is how an event gets stolen from a worker that was doing fine.
        """
        with self._engine.begin() as conn:
            conn.execute(
                update(pipeline_events)
                .where(pipeline_events.c.id == event_id, pipeline_events.c.status == "claimed")
                .values(claimed_at=_now())
            )

    def release(self, event_id: int, error: str = "") -> None:
        """Return a failed event to the queue for another attempt, recording why."""
        with self._engine.begin() as conn:
            conn.execute(
                update(pipeline_events)
                .where(pipeline_events.c.id == event_id)
                .values(status="queued", claimed_by=None, claimed_at=None, last_error=error[:2000])
            )

    def fail(self, event_id: int, error: str = "") -> None:
        """Dead-letter an event: terminal, with the reason kept.

        Deliberately a distinct status rather than deletion or a silent drop. The reported bug took
        hours to find because a stalled pipeline was indistinguishable from a slow one; an event
        that has given up must be able to say so.
        """
        with self._engine.begin() as conn:
            conn.execute(
                update(pipeline_events)
                .where(pipeline_events.c.id == event_id)
                .values(status="failed", claimed_at=None, last_error=error[:2000])
            )

    def failed_events(self, limit: int = 50) -> Sequence[dict[str, Any]]:
        # `Sequence`, not `list`: this class defines a `list` METHOD, which shadows the builtin
        # inside the class body.
        """Dead-lettered events, newest first — what `pipeline status` shows an operator."""
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(pipeline_events)
                    .where(pipeline_events.c.status == "failed")
                    .order_by(pipeline_events.c.id.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [
            {
                "id": int(r["id"]),
                "correlation_id": r["correlation_id"],
                "attempts": int(r["attempts"] or 0),
                "last_error": r["last_error"] or "",
            }
            for r in rows
        ]

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
        "input": s.input,
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
        input=r.get("input", ""),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        timeline=[_dict_to_tl(d) for d in json.loads(r["timeline"])],
    )


def _tl_to_dict(t: TimelineEntry) -> dict[str, Any]:
    row = {"seq": t.seq, "at": t.at, "stage_id": t.stage_id, "kind": t.kind, "detail": t.detail}
    if t.meta:
        # Only written when non-empty, so existing rows stay byte-identical and a reader on an
        # older build simply does not see the key.
        row["meta"] = t.meta
    return row


def _dict_to_tl(d: dict[str, Any]) -> TimelineEntry:
    return TimelineEntry(
        seq=d["seq"],
        at=d["at"],
        stage_id=d.get("stage_id"),
        kind=d["kind"],
        detail=d.get("detail", ""),
        meta=d.get("meta") or {},
    )


_STATUSES: tuple[SagaStatus, ...] = ("active", "parked", "completed", "rejected", "failed")

__all__ = ["SqlSagaStore", "metadata", "pipeline_events", "pipeline_saga", "pipeline_saga_seen"]
