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
from uuid import uuid4

from swarmkit_control_plane._models import Command, CommandStatus, Health, Instance

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    connection TEXT NOT NULL DEFAULT 'direct',
    token_ref TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'read',
    schema_version TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '{}',
    health TEXT NOT NULL DEFAULT 'unknown',
    last_seen TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commands (
    cmd_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    verb TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    output TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    dispatched_at TEXT,
    result_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_instance ON commands (instance_id, status);
"""


class SqliteRegistry:
    """Thread-safe sqlite-backed instance registry."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema (pre-existing DBs)."""
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(instances)").fetchall()}
        if "tier" not in cols:
            conn.execute("ALTER TABLE instances ADD COLUMN tier TEXT NOT NULL DEFAULT 'read'")

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
                   (id, name, endpoint, connection, token_ref, tier, schema_version,
                    capabilities, health, last_seen, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    instance.id,
                    instance.name,
                    instance.endpoint,
                    instance.connection,
                    instance.token_ref,
                    instance.tier,
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
            tier=row["tier"],
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

    # --- Mode B command queue -------------------------------------------------

    def _row_to_command(self, row: sqlite3.Row) -> Command:
        return Command(
            cmd_id=row["cmd_id"],
            instance_id=row["instance_id"],
            verb=row["verb"],
            args=json.loads(row["args"]),
            status=row["status"],
            output=json.loads(row["output"]) if row["output"] is not None else None,
            error=row["error"],
            created_at=row["created_at"],
            dispatched_at=row["dispatched_at"],
            result_at=row["result_at"],
        )

    def enqueue(self, instance_id: str, verb: str, args: dict[str, object]) -> Command:
        """Queue a command for a poll-connected instance."""
        cmd = Command(
            cmd_id=uuid4().hex[:12],
            instance_id=instance_id,
            verb=verb,
            args=dict(args),
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO commands (cmd_id, instance_id, verb, args, status, created_at)
                   VALUES (?, ?, ?, ?, 'queued', ?)""",
                (cmd.cmd_id, cmd.instance_id, cmd.verb, json.dumps(cmd.args), cmd.created_at),
            )
        return cmd

    def claim_queued(self, instance_id: str, limit: int = 50) -> list[Command]:
        """Atomically move this instance's queued commands to 'dispatched' and return them."""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM commands WHERE instance_id = ? AND status = 'queued'
                   ORDER BY created_at LIMIT ?""",
                (instance_id, limit),
            ).fetchall()
            cmds = [self._row_to_command(r) for r in rows]
            for cmd in cmds:
                conn.execute(
                    "UPDATE commands SET status = 'dispatched', dispatched_at = ? WHERE cmd_id = ?",
                    (now, cmd.cmd_id),
                )
                cmd.status = "dispatched"
                cmd.dispatched_at = now
        return cmds

    def record_result(
        self,
        cmd_id: str,
        *,
        status: CommandStatus,
        output: dict[str, object] | None = None,
        error: str | None = None,
    ) -> bool:
        """Record a command's terminal result. Idempotent: a no-op if already terminal.

        Returns True if this call set the result, False if it was already recorded.
        """
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT status FROM commands WHERE cmd_id = ?", (cmd_id,)).fetchone()
            if row is None:
                return False
            if row["status"] in ("done", "error"):
                return False  # at-least-once dedup: result already recorded
            conn.execute(
                "UPDATE commands SET status = ?, output = ?, error = ?, result_at = ? "
                "WHERE cmd_id = ?",
                (
                    status,
                    json.dumps(output) if output is not None else None,
                    error,
                    now,
                    cmd_id,
                ),
            )
        return True

    def get_command(self, cmd_id: str) -> Command | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM commands WHERE cmd_id = ?", (cmd_id,)).fetchone()
        return self._row_to_command(row) if row else None

    def list_commands(self, instance_id: str, limit: int = 100) -> list[Command]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM commands WHERE instance_id = ? ORDER BY created_at DESC LIMIT ?",
                (instance_id, limit),
            ).fetchall()
        return [self._row_to_command(r) for r in rows]
