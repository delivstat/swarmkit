"""SQLite persistence for jobs, conversations, and token usage.

Single database at ``.swarmkit/store.sqlite`` holds all runtime state
that should survive server restarts. WAL mode for concurrent access.

See design/details/distributed-architecture.md — this is Step 1
(single-process Postgres-ready via SQLite first).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swarmkit_runtime._sqlite import wal_connection

logger = logging.getLogger("swarmkit.persistence")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    topology TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    input TEXT NOT NULL,
    version TEXT,
    output TEXT,
    error TEXT,
    events TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    usage_input_tokens INTEGER DEFAULT 0,
    usage_output_tokens INTEGER DEFAULT 0,
    usage_cost_usd REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    topology TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    turns TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS run_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    conversation_id TEXT,
    agent_id TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS serve_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    action TEXT,
    status INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_run_usage_job ON run_usage(job_id);
CREATE INDEX IF NOT EXISTS idx_run_usage_conv ON run_usage(conversation_id);
CREATE INDEX IF NOT EXISTS idx_serve_access_created ON serve_access(created_at);
CREATE INDEX IF NOT EXISTS idx_serve_access_client ON serve_access(client_id);
"""


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


class SqliteStore:
    """Unified SQLite persistence for jobs, conversations, and usage.

    Thread-safe. WAL mode enabled for concurrent reads.

    Parameters
    ----------
    workspace_path:
        Root of the workspace directory. Database lives at
        ``{workspace_path}/.swarmkit/store.sqlite``.
    """

    def __init__(self, workspace_path: Path) -> None:
        db_dir = workspace_path / ".swarmkit"
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = db_dir / "store.sqlite"
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return wal_connection(self._db_path, timeout=10, foreign_keys=True, row_factory=sqlite3.Row)

    # ---- Jobs ----------------------------------------------------------------

    def create_job(self, job_id: str, topology: str, user_input: str) -> JobRow:
        now = datetime.now(UTC).isoformat()
        row = JobRow(
            id=job_id,
            topology=topology,
            status="pending",
            input=user_input,
            created_at=now,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, topology, status, input, created_at) VALUES (?, ?, ?, ?, ?)",
                (row.id, row.topology, row.status, row.input, row.created_at),
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
        updates: list[str] = []
        params: list[Any] = []
        for col, val in [
            ("status", status),
            ("output", output),
            ("error", error),
            ("version", version),
            ("completed_at", completed_at),
            ("usage_input_tokens", usage_input_tokens),
            ("usage_output_tokens", usage_output_tokens),
            ("usage_cost_usd", usage_cost_usd),
        ]:
            if val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if events is not None:
            updates.append("events = ?")
            params.append(json.dumps(events))
        if not updates:
            return
        params.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"
        with self._lock, self._connect() as conn:
            conn.execute(sql, params)

    def get_job(self, job_id: str) -> JobRow | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(self, limit: int = 100) -> list[JobRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRow:
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
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, topology, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (row.id, row.topology, row.created_at, row.updated_at),
            )
        return row

    def get_conversation(self, conv_id: str) -> ConversationRow | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_conv(row)

    def update_conversation(
        self,
        conv_id: str,
        turns: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            params: list[Any] = [json.dumps(turns, default=str), now]
            if metadata is not None:
                conn.execute(
                    "UPDATE conversations SET turns = ?, updated_at = ?, metadata = ? WHERE id = ?",
                    [*params, json.dumps(metadata, default=str), conv_id],
                )
            else:
                conn.execute(
                    "UPDATE conversations SET turns = ?, updated_at = ? WHERE id = ?",
                    [*params, conv_id],
                )

    def list_conversations(self, limit: int = 50) -> list[ConversationRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_conv(r) for r in rows]

    def delete_conversation(self, conv_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_conv(row: sqlite3.Row) -> ConversationRow:
        turns = json.loads(row["turns"]) if row["turns"] else []
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        return ConversationRow(
            id=row["id"],
            topology=row["topology"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turns=turns,
            metadata=metadata,
        )

    # ---- Usage tracking ------------------------------------------------------

    def record_usage(self, usage: UsageRow) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO run_usage
                   (job_id, conversation_id, agent_id, model,
                    input_tokens, output_tokens, cache_read_tokens,
                    cost_usd, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    usage.job_id,
                    usage.conversation_id,
                    usage.agent_id,
                    usage.model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_read_tokens,
                    usage.cost_usd,
                    now,
                ),
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
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO serve_access
                   (client_id, provider, method, path, action, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (client_id, provider, method, path, action, status, now),
            )

    def list_access(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent serve access-audit records, newest first."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT client_id, provider, method, path, action, status, created_at "
                "FROM serve_access ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_usage_summary(
        self,
        *,
        job_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        where_parts: list[str] = []
        params: list[Any] = []
        if job_id:
            where_parts.append("job_id = ?")
            params.append(job_id)
        if conversation_id:
            where_parts.append("conversation_id = ?")
            params.append(conversation_id)
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        sql = f"""
            SELECT
                COUNT(*) as total_calls,
                COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) as total_cache_tokens,
                COALESCE(SUM(cost_usd), 0.0) as total_cost_usd
            FROM run_usage {where_clause}
        """
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
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
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT model,
                    COUNT(*) as calls,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(cost_usd) as cost_usd
                 FROM run_usage
                 GROUP BY model
                 ORDER BY cost_usd DESC"""
            ).fetchall()
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


__all__ = ["ConversationRow", "JobRow", "SqliteStore", "UsageRow"]
