"""SQLAlchemy-Core persistence for jobs, conversations, usage, and serve access-audit.

One implementation drives both SQLite (default) and Postgres (design/details/postgres-backend.md):
``Store`` is built from a SQLAlchemy URL, ``SqliteStore(workspace_path)`` is the back-compat
constructor that points it at ``{workspace}/.swarmkit/store.sqlite``. Method signatures + return
shapes are identical to the previous raw-sqlite store, so callers and tests are unchanged.

WAL + busy_timeout are set per-connection for the SQLite dialect only (via a ``connect`` event),
preserving the prior concurrency behaviour. Timestamps + JSON ride as Text, unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, delete, event, func, insert, select, update
from sqlalchemy.engine import RowMapping

from swarmkit_runtime.persistence._tables import (
    conversations,
    jobs,
    metadata,
    run_usage,
    serve_access,
)


def normalize_url(url: str) -> str:
    """Point bare ``postgres://`` / ``postgresql://`` URLs at the psycopg 3 driver.

    A ``DATABASE_URL`` is commonly ``postgresql://…``, which SQLAlchemy maps to psycopg2 (not a
    dependency here); rewrite it to ``postgresql+psycopg://…`` so it uses the installed psycopg 3.
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str) -> Engine:
    """Create a SQLAlchemy engine, enabling WAL + busy_timeout + FKs for the SQLite dialect."""
    engine = create_engine(normalize_url(url))
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=10000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


@dataclass
class JobRow:
    """A persisted job."""

    id: str
    topology: str
    status: str
    input: str
    version: str | None = None
    output: str | None = None
    error: str | None = None
    events: list[str] = field(default_factory=list)
    created_at: str = ""
    completed_at: str | None = None
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0
    usage_cost_usd: float = 0.0


@dataclass
class ConversationRow:
    """A persisted conversation."""

    id: str
    topology: str
    created_at: str
    updated_at: str
    turns: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageRow:
    """Token usage record for a single LLM call."""

    agent_id: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    job_id: str | None = None
    conversation_id: str | None = None


class Store:
    """SQLAlchemy-backed persistence over any supported dialect (SQLite / Postgres).

    Thread-safe via the engine's connection pool. Construct from a SQLAlchemy URL, or use the
    :class:`SqliteStore` subclass for the file-based default.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        metadata.create_all(engine)

    @property
    def engine(self) -> Engine:
        return self._engine

    # ---- Jobs ----------------------------------------------------------------

    def create_job(self, job_id: str, topology: str, user_input: str) -> JobRow:
        now = datetime.now(UTC).isoformat()
        row = JobRow(
            id=job_id, topology=topology, status="pending", input=user_input, created_at=now
        )
        with self._engine.begin() as conn:
            conn.execute(
                insert(jobs).values(
                    id=row.id,
                    topology=row.topology,
                    status=row.status,
                    input=row.input,
                    created_at=row.created_at,
                )
            )
        return row

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        output: str | None = None,
        error: str | None = None,
        version: str | None = None,
        completed_at: str | None = None,
        events: list[str] | None = None,
        usage_input_tokens: int | None = None,
        usage_output_tokens: int | None = None,
        usage_cost_usd: float | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        for col, val in (
            ("status", status),
            ("output", output),
            ("error", error),
            ("version", version),
            ("completed_at", completed_at),
            ("usage_input_tokens", usage_input_tokens),
            ("usage_output_tokens", usage_output_tokens),
            ("usage_cost_usd", usage_cost_usd),
        ):
            if val is not None:
                values[col] = val
        if events is not None:
            values["events"] = json.dumps(events)
        if not values:
            return
        with self._engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(**values))

    def get_job(self, job_id: str) -> JobRow | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).mappings().first()
        return self._row_to_job(row) if row is not None else None

    def list_jobs(self, limit: int = 100) -> list[JobRow]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(select(jobs).order_by(jobs.c.created_at.desc()).limit(limit))
                .mappings()
                .all()
            )
        return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(row: RowMapping) -> JobRow:
        events = json.loads(row["events"]) if row["events"] else []
        return JobRow(
            id=row["id"],
            topology=row["topology"],
            status=row["status"],
            input=row["input"],
            version=row["version"],
            output=row["output"],
            error=row["error"],
            events=events,
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            usage_input_tokens=row["usage_input_tokens"] or 0,
            usage_output_tokens=row["usage_output_tokens"] or 0,
            usage_cost_usd=row["usage_cost_usd"] or 0.0,
        )

    # ---- Conversations -------------------------------------------------------

    def create_conversation(self, conv_id: str, topology: str) -> ConversationRow:
        now = datetime.now(UTC).isoformat()
        row = ConversationRow(id=conv_id, topology=topology, created_at=now, updated_at=now)
        with self._engine.begin() as conn:
            conn.execute(
                insert(conversations).values(
                    id=row.id, topology=row.topology, created_at=row.created_at, updated_at=now
                )
            )
        return row

    def get_conversation(self, conv_id: str) -> ConversationRow | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(select(conversations).where(conversations.c.id == conv_id))
                .mappings()
                .first()
            )
        return self._row_to_conv(row) if row is not None else None

    def update_conversation(
        self,
        conv_id: str,
        turns: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        values: dict[str, Any] = {"turns": json.dumps(turns, default=str), "updated_at": now}
        if metadata is not None:
            values["metadata"] = json.dumps(metadata, default=str)
        with self._engine.begin() as conn:
            conn.execute(
                update(conversations).where(conversations.c.id == conv_id).values(**values)
            )

    def list_conversations(self, limit: int = 50) -> list[ConversationRow]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(conversations).order_by(conversations.c.updated_at.desc()).limit(limit)
                )
                .mappings()
                .all()
            )
        return [self._row_to_conv(r) for r in rows]

    def delete_conversation(self, conv_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(delete(conversations).where(conversations.c.id == conv_id))
        return result.rowcount > 0

    @staticmethod
    def _row_to_conv(row: RowMapping) -> ConversationRow:
        return ConversationRow(
            id=row["id"],
            topology=row["topology"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turns=json.loads(row["turns"]) if row["turns"] else [],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    # ---- Usage tracking ------------------------------------------------------

    def record_usage(self, usage: UsageRow) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(run_usage).values(
                    job_id=usage.job_id,
                    conversation_id=usage.conversation_id,
                    agent_id=usage.agent_id,
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cost_usd=usage.cost_usd,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )

    def record_access(
        self,
        *,
        client_id: str,
        provider: str,
        method: str,
        path: str,
        action: str | None,
        status: int,
    ) -> None:
        """Append a serve access-audit record (who called what over the API)."""
        with self._engine.begin() as conn:
            conn.execute(
                insert(serve_access).values(
                    client_id=client_id,
                    provider=provider,
                    method=method,
                    path=path,
                    action=action,
                    status=status,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )

    def list_access(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent serve access-audit records, newest first."""
        cols = (
            serve_access.c.client_id,
            serve_access.c.provider,
            serve_access.c.method,
            serve_access.c.path,
            serve_access.c.action,
            serve_access.c.status,
            serve_access.c.created_at,
        )
        with self._engine.connect() as conn:
            rows = (
                conn.execute(select(*cols).order_by(serve_access.c.id.desc()).limit(limit))
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def get_usage_summary(
        self,
        *,
        job_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        stmt = select(
            func.count().label("total_calls"),
            func.coalesce(func.sum(run_usage.c.input_tokens), 0).label("total_input_tokens"),
            func.coalesce(func.sum(run_usage.c.output_tokens), 0).label("total_output_tokens"),
            func.coalesce(func.sum(run_usage.c.cache_read_tokens), 0).label("total_cache_tokens"),
            func.coalesce(func.sum(run_usage.c.cost_usd), 0.0).label("total_cost_usd"),
        )
        if job_id:
            stmt = stmt.where(run_usage.c.job_id == job_id)
        if conversation_id:
            stmt = stmt.where(run_usage.c.conversation_id == conversation_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        if row is None:
            return {
                "total_calls": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_tokens": 0,
                "total_cost_usd": 0.0,
            }
        return {
            "total_calls": row["total_calls"],
            "total_input_tokens": row["total_input_tokens"],
            "total_output_tokens": row["total_output_tokens"],
            "total_cache_tokens": row["total_cache_tokens"],
            "total_cost_usd": round(row["total_cost_usd"], 6),
        }

    def get_usage_by_model(self) -> list[dict[str, Any]]:
        stmt = (
            select(
                run_usage.c.model,
                func.count().label("calls"),
                func.sum(run_usage.c.input_tokens).label("input_tokens"),
                func.sum(run_usage.c.output_tokens).label("output_tokens"),
                func.sum(run_usage.c.cost_usd).label("cost_usd"),
            )
            .group_by(run_usage.c.model)
            .order_by(func.sum(run_usage.c.cost_usd).desc())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [
            {
                "model": r["model"],
                "calls": r["calls"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cost_usd": round(r["cost_usd"], 6),
            }
            for r in rows
        ]


class SqliteStore(Store):
    """File-based store — the default backend, at ``{workspace}/.swarmkit/store.sqlite``.

    Kept as the back-compat constructor (many call sites build ``SqliteStore(workspace_path)``);
    it is a plain SQLite-dialect :class:`Store`.
    """

    def __init__(self, workspace_path: Path) -> None:
        db_dir = workspace_path / ".swarmkit"
        db_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(make_engine(f"sqlite:///{db_dir / 'store.sqlite'}"))


__all__ = [
    "ConversationRow",
    "JobRow",
    "SqliteStore",
    "Store",
    "UsageRow",
    "make_engine",
    "normalize_url",
]
