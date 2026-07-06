"""SQLAlchemy-Core audit provider — the default persistent backend for SQLite and Postgres.

One implementation drives both dialects (see postgres-backend.md): ``SQLiteAuditProvider`` points
it at a local file, ``PostgresAuditProvider`` at a URL. The methods keep the async
``AuditProvider`` signatures but do synchronous SQLAlchemy work — the same execution model as the
sync-sqlite provider, so the sync/async boundary (the ``Observability`` facade, ``close_sync``) is
unchanged and event-loop-safe. Moving the DB work fully off the loop (``asyncio.to_thread``) is a
separate, optional hardening.

Append-only (§8.3): only INSERT (dedup by PK) + a retention DELETE are issued — no UPDATE.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, delete, func, insert, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError

from swarmkit_runtime.audit._provider import AuditProvider
from swarmkit_runtime.audit._tables import audit_events, audit_metadata
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.persistence._store import make_engine


class SqlAuditProvider(AuditProvider):
    """SQLAlchemy-backed audit storage over any supported dialect (SQLite / Postgres)."""

    provider_id = "sql"

    def __init__(self, engine: Engine, retention_days: int = 365) -> None:
        self._engine = engine
        self._retention_days = retention_days
        audit_metadata.create_all(engine)

    async def record(self, event: AuditEvent) -> None:
        values = {
            "event_id": str(event.event_id),
            "event_type": event.event_type,
            "agent_id": event.agent_id,
            "timestamp": event.timestamp.isoformat(),
            "run_id": event.run_id,
            "parent_event_id": str(event.parent_event_id) if event.parent_event_id else None,
            "topology_id": event.topology_id,
            "skill_id": event.skill_id,
            "agent_role": event.agent_role,
            "skill_category": event.skill_category,
            "inputs": _dumps(event.inputs),
            "outputs": _dumps(event.outputs),
            "verdict": event.verdict,
            "reasoning": event.reasoning,
            "confidence": event.confidence,
            "model_provider": event.model_provider,
            "model_name": event.model_name,
            "tokens_in": event.tokens_in,
            "tokens_out": event.tokens_out,
            "cost_usd": event.cost_usd,
            "duration_ms": event.duration_ms,
            "policy_decision": event.policy_decision,
            "policy_reason": event.policy_reason,
            "error": _dumps(event.error),
            "payload": _dumps(event.payload),
        }
        try:
            with self._engine.begin() as conn:
                conn.execute(insert(audit_events).values(**values))
        except IntegrityError:
            pass  # duplicate event_id (PK) — append-only dedup, never raise (per the ABC)

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
        stmt = select(audit_events)
        if run_id:
            stmt = stmt.where(audit_events.c.run_id == run_id)
        if agent_id:
            stmt = stmt.where(audit_events.c.agent_id == agent_id)
        if event_type:
            stmt = stmt.where(audit_events.c.event_type == event_type)
        if since:
            stmt = stmt.where(audit_events.c.timestamp >= since.isoformat())
        if until:
            stmt = stmt.where(audit_events.c.timestamp <= until.isoformat())
        stmt = stmt.order_by(audit_events.c.timestamp.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        for row in rows:
            yield _row_to_event(row)

    async def count(
        self,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        event_type: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(audit_events)
        if run_id:
            stmt = stmt.where(audit_events.c.run_id == run_id)
        if agent_id:
            stmt = stmt.where(audit_events.c.agent_id == agent_id)
        if event_type:
            stmt = stmt.where(audit_events.c.event_type == event_type)
        with self._engine.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    async def prune_expired(self) -> int:
        """Remove events older than retention_days. Returns count deleted."""
        cutoff = (datetime.now(tz=UTC) - timedelta(days=self._retention_days)).isoformat()
        with self._engine.begin() as conn:
            result = conn.execute(delete(audit_events).where(audit_events.c.timestamp < cutoff))
        return result.rowcount

    async def close(self) -> None:
        self._engine.dispose()

    def close_sync(self) -> None:
        """Synchronous close for use in non-async CLI contexts (the Observability facade)."""
        self._engine.dispose()


class SQLiteAuditProvider(SqlAuditProvider):
    """Local SQLite audit storage (default) at the given file path.

    Kept as the back-compat constructor: ``SQLiteAuditProvider(db_path=…, retention_days=…)``.
    """

    provider_id = "sqlite"

    def __init__(self, db_path: str | Path, retention_days: int = 365) -> None:
        super().__init__(make_engine(f"sqlite:///{Path(db_path)}"), retention_days)


class PostgresAuditProvider(SqlAuditProvider):
    """Postgres audit storage for distributed deployments, from a SQLAlchemy URL."""

    provider_id = "postgres"

    def __init__(self, url: str, retention_days: int = 365) -> None:
        super().__init__(make_engine(url), retention_days)


def audit_provider_for_path(path: Path) -> SqlAuditProvider:
    """Resolve the audit backend for a workspace from env config (mirrors the store factory).

    Postgres when ``SWARMKIT_STORE_BACKEND=postgres`` + a URL is set (audit shares the runtime's
    ``DATABASE_URL``, so it follows the same backend as the persistence store); else the local
    ``.swarmkit/audit.sqlite`` file.
    """
    from swarmkit_runtime.persistence._factory import _resolve_backend  # noqa: PLC0415

    root = path.resolve()
    backend, url = _resolve_backend(root)
    if backend == "postgres":
        return PostgresAuditProvider(url)
    return SQLiteAuditProvider(db_path=root / ".swarmkit" / "audit.sqlite")


def _dumps(value: Any) -> str | None:
    import json  # noqa: PLC0415

    return json.dumps(value) if value else None


def _row_to_event(row: RowMapping) -> AuditEvent:
    import json  # noqa: PLC0415

    def _loads(v: Any) -> Any:
        return json.loads(v) if v else None

    return AuditEvent(
        event_id=UUID(row["event_id"]),
        event_type=row["event_type"],
        agent_id=row["agent_id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        run_id=row["run_id"],
        parent_event_id=UUID(row["parent_event_id"]) if row["parent_event_id"] else None,
        topology_id=row["topology_id"],
        skill_id=row["skill_id"],
        agent_role=row["agent_role"],
        skill_category=row["skill_category"],
        inputs=_loads(row["inputs"]),
        outputs=_loads(row["outputs"]),
        verdict=row["verdict"],
        reasoning=row["reasoning"],
        confidence=row["confidence"],
        model_provider=row["model_provider"],
        model_name=row["model_name"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cost_usd=row["cost_usd"],
        duration_ms=row["duration_ms"],
        policy_decision=row["policy_decision"],
        policy_reason=row["policy_reason"],
        error=_loads(row["error"]),
        payload=_loads(row["payload"]) or {},
    )
