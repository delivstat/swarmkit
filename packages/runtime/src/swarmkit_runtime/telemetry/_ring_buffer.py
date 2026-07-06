"""Local ring buffer — SQLite-backed prompt/response store.

Persists LLM prompt/response pairs keyed by OTel span ID. Prompts
never leave the user's environment — this is the "Privacy-First
Debugger" from design/details/product-architecture-refinements.md.

The Rynko dashboard shows structural OTel traces; this buffer stores
the actual content locally for `swarmkit debug --span-id <id>`.

Storage: .swarmkit/prompts.sqlite (survives process restarts).
Retention: configurable TTL (default 7 days) with run-count fallback.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from swarmkit_runtime._sqlite import bootstrap, wal_connection

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS prompts (
    span_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    response TEXT NOT NULL,
    model TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    metadata TEXT
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_prompts_run_id ON prompts(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_prompts_agent_id ON prompts(agent_id)",
    "CREATE INDEX IF NOT EXISTS idx_prompts_timestamp ON prompts(timestamp DESC)",
]


class PromptRingBuffer:
    """Local SQLite store for prompt/response pairs.

    Keyed by OTel span_id for correlation with cloud traces.
    Survives process restarts. Pruned by TTL or run count.
    """

    def __init__(
        self,
        db_path: str | Path = ".swarmkit/prompts.sqlite",
        retention_days: int = 7,
        max_entries: int | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._retention_days = retention_days
        self._max_entries = max_entries
        self._conn = wal_connection(self._db_path, check_same_thread=False, synchronous="NORMAL")
        bootstrap(self._conn, _CREATE_TABLE, _CREATE_INDEXES)

    def store(
        self,
        *,
        span_id: str,
        run_id: str,
        agent_id: str,
        step: int,
        prompt: str,
        response: str,
        model: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store a prompt/response pair. Idempotent (ignores duplicate span_id)."""
        self._conn.execute(
            """INSERT OR IGNORE INTO prompts
               (span_id, run_id, agent_id, step, prompt, response, model, timestamp, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                span_id,
                run_id,
                agent_id,
                step,
                prompt,
                response,
                model,
                datetime.now(tz=UTC).isoformat(),
                json.dumps(metadata) if metadata else None,
            ),
        )
        self._conn.commit()

    def query_by_span_id(self, span_id: str) -> dict[str, Any] | None:
        """Retrieve a single prompt/response by span ID."""
        cursor = self._conn.execute("SELECT * FROM prompts WHERE span_id = ?", (span_id,))
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None

    def query_by_run_id(self, run_id: str) -> list[dict[str, Any]]:
        """Retrieve all prompts for a run, ordered by step."""
        cursor = self._conn.execute(
            "SELECT * FROM prompts WHERE run_id = ? ORDER BY step ASC", (run_id,)
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]

    def query_by_agent(self, agent_id: str, last_n: int = 5) -> list[dict[str, Any]]:
        """Retrieve the last N prompts for an agent."""
        cursor = self._conn.execute(
            "SELECT * FROM prompts WHERE agent_id = ? ORDER BY timestamp DESC LIMIT ?",
            (agent_id, last_n),
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]

    def prune_expired(self) -> int:
        """Remove entries older than retention_days. Returns count deleted."""
        cutoff = (datetime.now(tz=UTC) - timedelta(days=self._retention_days)).isoformat()
        cursor = self._conn.execute("DELETE FROM prompts WHERE timestamp < ?", (cutoff,))
        self._conn.commit()
        deleted = cursor.rowcount

        if self._max_entries is not None:
            overflow_cursor = self._conn.execute(
                """DELETE FROM prompts WHERE span_id IN (
                    SELECT span_id FROM prompts ORDER BY timestamp DESC
                    LIMIT -1 OFFSET ?
                )""",
                (self._max_entries,),
            )
            self._conn.commit()
            deleted += overflow_cursor.rowcount

        return deleted

    def count(self) -> int:
        """Total entries in the buffer."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM prompts")
        result: int = cursor.fetchone()[0]
        return result

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert a database row to a dictionary."""
    return {
        "span_id": row[0],
        "run_id": row[1],
        "agent_id": row[2],
        "step": row[3],
        "prompt": row[4],
        "response": row[5],
        "model": row[6],
        "timestamp": row[7],
        "metadata": json.loads(row[8]) if row[8] else None,
    }
