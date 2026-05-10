"""SQLite-backed AuditProvider — the default persistent backend.

Stores audit events in a local SQLite database. Suitable for single-node
deployments and development. Production multi-node setups should use the
postgres provider (future).

Schema is append-only by design — no UPDATE or DELETE statements exist.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from swarmkit_runtime.audit._provider import AuditProvider
from swarmkit_runtime.governance import AuditEvent

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    run_id TEXT,
    parent_event_id TEXT,
    topology_id TEXT,
    skill_id TEXT,
    agent_role TEXT,
    skill_category TEXT,
    inputs TEXT,
    outputs TEXT,
    verdict TEXT,
    reasoning TEXT,
    confidence REAL,
    model_provider TEXT,
    model_name TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL,
    duration_ms INTEGER,
    policy_decision TEXT,
    policy_reason TEXT,
    error TEXT,
    payload TEXT
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_run_id ON audit_events(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_agent_id ON audit_events(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_events(event_type)",
]


class SQLiteAuditProvider(AuditProvider):
    """Local SQLite audit storage.

    Config:
        path: str — database file path (default: .swarmkit/audit.sqlite)
        retention_days: int — auto-prune events older than this (default: 365)
    """

    provider_id = "sqlite"

    def __init__(self, db_path: str | Path, retention_days: int = 365) -> None:
        self._db_path = Path(db_path)
        self._retention_days = retention_days
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(_CREATE_TABLE)
        for idx in _CREATE_INDEXES:
            self._conn.execute(idx)
        self._conn.commit()

    async def record(self, event: AuditEvent) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO audit_events (
                event_id, event_type, agent_id, timestamp, run_id,
                parent_event_id, topology_id, skill_id, agent_role,
                skill_category, inputs, outputs, verdict, reasoning,
                confidence, model_provider, model_name, tokens_in,
                tokens_out, cost_usd, duration_ms, policy_decision,
                policy_reason, error, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(event.event_id),
                event.event_type,
                event.agent_id,
                event.timestamp.isoformat(),
                event.run_id,
                str(event.parent_event_id) if event.parent_event_id else None,
                event.topology_id,
                event.skill_id,
                event.agent_role,
                event.skill_category,
                json.dumps(event.inputs) if event.inputs else None,
                json.dumps(event.outputs) if event.outputs else None,
                event.verdict,
                event.reasoning,
                event.confidence,
                event.model_provider,
                event.model_name,
                event.tokens_in,
                event.tokens_out,
                event.cost_usd,
                event.duration_ms,
                event.policy_decision,
                event.policy_reason,
                json.dumps(event.error) if event.error else None,
                json.dumps(event.payload) if event.payload else None,
            ),
        )
        self._conn.commit()

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
        conditions: list[str] = []
        params: list[Any] = []

        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if since:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())
        if until:
            conditions.append("timestamp <= ?")
            params.append(until.isoformat())

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM audit_events {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(sql, params)
        for row in cursor.fetchall():
            yield _row_to_event(row)

    async def count(
        self,
        *,
        run_id: str | None = None,
        agent_id: str | None = None,
        event_type: str | None = None,
    ) -> int:
        conditions: list[str] = []
        params: list[Any] = []

        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)
        if agent_id:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT COUNT(*) FROM audit_events {where}"

        cursor = self._conn.execute(sql, params)
        result: int = cursor.fetchone()[0]
        return result

    async def prune_expired(self) -> int:
        """Remove events older than retention_days. Returns count deleted."""
        cutoff = datetime.now(tz=UTC).isoformat()
        # Calculate cutoff by subtracting retention days
        from datetime import timedelta  # noqa: PLC0415

        cutoff_dt = datetime.now(tz=UTC) - timedelta(days=self._retention_days)
        cutoff = cutoff_dt.isoformat()

        cursor = self._conn.execute("DELETE FROM audit_events WHERE timestamp < ?", (cutoff,))
        self._conn.commit()
        return cursor.rowcount

    async def close(self) -> None:
        self._conn.close()


def _row_to_event(row: tuple[Any, ...]) -> AuditEvent:
    """Convert a database row to an AuditEvent."""
    return AuditEvent(
        event_id=UUID(row[0]),
        event_type=row[1],
        agent_id=row[2],
        timestamp=datetime.fromisoformat(row[3]),
        run_id=row[4],
        parent_event_id=UUID(row[5]) if row[5] else None,
        topology_id=row[6],
        skill_id=row[7],
        agent_role=row[8],
        skill_category=row[9],
        inputs=json.loads(row[10]) if row[10] else None,
        outputs=json.loads(row[11]) if row[11] else None,
        verdict=row[12],
        reasoning=row[13],
        confidence=row[14],
        model_provider=row[15],
        model_name=row[16],
        tokens_in=row[17],
        tokens_out=row[18],
        cost_usd=row[19],
        duration_ms=row[20],
        policy_decision=row[21],
        policy_reason=row[22],
        error=json.loads(row[23]) if row[23] else None,
        payload=json.loads(row[24]) if row[24] else {},
    )
