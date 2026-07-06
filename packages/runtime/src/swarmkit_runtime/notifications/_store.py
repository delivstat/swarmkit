"""NotificationStore — SQLite-backed persistence for notification history.

All notifications are persisted with delivery status. Both the CLI
(`swarmkit notifications`) and the web UI read from this store via
WorkspaceRuntime. External delivery (Slack, Discord, etc.) is a
side effect — the primary record lives here.

Storage: .swarmkit/notifications.sqlite
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from swarmkit_runtime._sqlite import bootstrap, wal_connection
from swarmkit_runtime.notifications._provider import NotificationEvent

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    run_id TEXT NOT NULL,
    topology_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    provider TEXT,
    delivered_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL
)
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_notif_run_id ON notifications(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_notif_status ON notifications(status)",
    "CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at DESC)",
]


class NotificationRecord:
    """A persisted notification with delivery status."""

    def __init__(self, row: tuple[Any, ...]) -> None:
        self.id: str = row[0]
        self.event_type: str = row[1]
        self.run_id: str = row[2]
        self.topology_id: str = row[3]
        self.summary: str = row[4]
        self.metadata: dict[str, Any] = json.loads(row[5]) if row[5] else {}
        self.status: str = row[6]
        self.provider: str | None = row[7]
        self.delivered_at: str | None = row[8]
        self.error: str | None = row[9]
        self.created_at: str = row[10]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "run_id": self.run_id,
            "topology_id": self.topology_id,
            "summary": self.summary,
            "status": self.status,
            "provider": self.provider,
            "delivered_at": self.delivered_at,
            "error": self.error,
            "created_at": self.created_at,
        }


class NotificationStore:
    """SQLite-backed notification persistence.

    Used by both CLI (`swarmkit notifications`) and web UI (via
    WorkspaceRuntime service layer). Same store, same data, same API.
    """

    def __init__(self, db_path: str | Path = ".swarmkit/notifications.sqlite") -> None:
        self._db_path = Path(db_path)
        self._conn = wal_connection(self._db_path, check_same_thread=False, synchronous="NORMAL")
        bootstrap(self._conn, _CREATE_TABLE, _CREATE_INDEXES)

    def create(self, event: NotificationEvent) -> str:
        """Persist a notification event. Returns the notification ID."""
        notif_id = str(uuid4())
        self._conn.execute(
            """INSERT INTO notifications
               (id, event_type, run_id, topology_id, summary, metadata, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                notif_id,
                event.event_type,
                event.run_id,
                event.topology_id,
                event.summary,
                json.dumps(event.metadata) if event.metadata else None,
                datetime.now(tz=UTC).isoformat(),
            ),
        )
        self._conn.commit()
        return notif_id

    def mark_delivered(self, notif_id: str, provider: str) -> None:
        """Mark a notification as successfully delivered."""
        self._conn.execute(
            "UPDATE notifications SET status='delivered', provider=?, delivered_at=? WHERE id=?",
            (provider, datetime.now(tz=UTC).isoformat(), notif_id),
        )
        self._conn.commit()

    def mark_failed(self, notif_id: str, provider: str, error: str) -> None:
        """Mark a notification as failed to deliver."""
        self._conn.execute(
            "UPDATE notifications SET status='failed', provider=?, error=? WHERE id=?",
            (provider, error, notif_id),
        )
        self._conn.commit()

    def query(
        self,
        *,
        status: str | None = None,
        run_id: str | None = None,
        limit: int = 20,
    ) -> list[NotificationRecord]:
        """Query notifications. Returns newest-first."""
        conditions: list[str] = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if run_id:
            conditions.append("run_id = ?")
            params.append(run_id)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM notifications {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(sql, params)
        return [NotificationRecord(row) for row in cursor.fetchall()]

    def count(self, *, status: str | None = None) -> int:
        """Count notifications, optionally filtered by status."""
        if status:
            cursor = self._conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE status = ?", (status,)
            )
        else:
            cursor = self._conn.execute("SELECT COUNT(*) FROM notifications")
        result: int = cursor.fetchone()[0]
        return result

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
