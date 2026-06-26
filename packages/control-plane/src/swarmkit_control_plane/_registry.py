"""SqliteRegistry — durable store of enrolled instances.

Sqlite for now (mirrors the runtime store); the design's central Postgres is a later swap.
See design/details/control-plane/04-persistence-state.md, 13-connector-registry.md.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from swarmkit_control_plane._models import Health, Instance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    connection TEXT NOT NULL DEFAULT 'direct',
    token_ref TEXT NOT NULL DEFAULT '',
    schema_version TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '{}',
    health TEXT NOT NULL DEFAULT 'unknown',
    last_seen TEXT,
    created_at TEXT NOT NULL
);
"""


class SqliteRegistry:
    """Thread-safe sqlite-backed instance registry."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def add(self, instance: Instance) -> None:
        if not instance.created_at:
            instance.created_at = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO instances
                   (id, name, endpoint, connection, token_ref, schema_version,
                    capabilities, health, last_seen, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    instance.id,
                    instance.name,
                    instance.endpoint,
                    instance.connection,
                    instance.token_ref,
                    instance.schema_version,
                    json.dumps(instance.capabilities),
                    instance.health,
                    instance.last_seen,
                    instance.created_at,
                ),
            )

    def _row_to_instance(self, row: sqlite3.Row) -> Instance:
        return Instance(
            id=row["id"],
            name=row["name"],
            endpoint=row["endpoint"],
            connection=row["connection"],
            token_ref=row["token_ref"],
            schema_version=row["schema_version"],
            capabilities=json.loads(row["capabilities"]),
            health=row["health"],
            last_seen=row["last_seen"],
            created_at=row["created_at"],
        )

    def get(self, instance_id: str) -> Instance | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM instances WHERE id = ?", (instance_id,)).fetchone()
        return self._row_to_instance(row) if row else None

    def list_all(self) -> list[Instance]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM instances ORDER BY created_at").fetchall()
        return [self._row_to_instance(r) for r in rows]

    def delete(self, instance_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
        return cur.rowcount > 0

    def update_health(
        self,
        instance_id: str,
        *,
        health: Health,
        schema_version: str | None = None,
        capabilities: dict[str, object] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        sets = ["health = ?", "last_seen = ?"]
        params: list[object] = [health, now]
        if schema_version is not None:
            sets.append("schema_version = ?")
            params.append(schema_version)
        if capabilities is not None:
            sets.append("capabilities = ?")
            params.append(json.dumps(capabilities))
        params.append(instance_id)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE instances SET {', '.join(sets)} WHERE id = ?", params)
