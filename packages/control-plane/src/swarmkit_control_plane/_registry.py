"""SqliteRegistry — durable store of enrolled instances + the Mode B command queue.

SQLAlchemy Core over SQLite (default) or Postgres (design/details/postgres-backend.md); the class
name is kept for its many import sites. See design/details/control-plane/04-persistence-state.md,
13-connector-registry.md.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.engine import Connection, RowMapping

from swarmkit_control_plane._models import Command, CommandStatus, Health, Instance
from swarmkit_control_plane._store_base import Store, upsert
from swarmkit_control_plane._tables import commands, instances

_INSTANCE_COLS = (
    "name",
    "endpoint",
    "connection",
    "token_ref",
    "tier",
    "token_fingerprint",
    "token_hash",
    "token_minted_at",
    "schema_version",
    "capabilities",
    "health",
    "last_seen",
    "created_at",
)


class SqliteRegistry(Store):
    """Thread-safe instance registry (SQLite or Postgres)."""

    def _migrate(self, conn: Connection) -> None:
        """Add columns introduced after the initial schema (pre-existing SQLite DBs).

        ``create_all`` builds the full schema for a fresh DB; this only fires for a database created
        by an older schema, where the table already exists so ``create_all`` skips it.
        """
        if self._engine.dialect.name != "sqlite":
            return
        cols = {c["name"] for c in inspect(self._engine).get_columns("instances")}
        if "tier" not in cols:
            conn.execute(text("ALTER TABLE instances ADD COLUMN tier TEXT NOT NULL DEFAULT 'read'"))
        if "token_fingerprint" not in cols:
            conn.execute(
                text("ALTER TABLE instances ADD COLUMN token_fingerprint TEXT NOT NULL DEFAULT ''")
            )
            conn.execute(text("ALTER TABLE instances ADD COLUMN token_minted_at TEXT"))
        if "token_hash" not in cols:
            conn.execute(
                text("ALTER TABLE instances ADD COLUMN token_hash TEXT NOT NULL DEFAULT ''")
            )

    def add(self, instance: Instance) -> None:
        if not instance.created_at:
            instance.created_at = datetime.now(UTC).isoformat()
        values = {
            "id": instance.id,
            "name": instance.name,
            "endpoint": instance.endpoint,
            "connection": instance.connection,
            "token_ref": instance.token_ref,
            "tier": instance.tier,
            "token_fingerprint": instance.token_fingerprint,
            "token_hash": instance.token_hash,
            "token_minted_at": instance.token_minted_at,
            "schema_version": instance.schema_version,
            "capabilities": json.dumps(instance.capabilities),
            "health": instance.health,
            "last_seen": instance.last_seen,
            "created_at": instance.created_at,
        }
        # INSERT OR REPLACE on the id primary key — re-enrolling refreshes every field.
        stmt = upsert(
            self._engine,
            instances,
            values,
            index_elements=["id"],
            set_={c: values[c] for c in _INSTANCE_COLS},
        )
        with self._lock, self._engine.begin() as conn:
            conn.execute(stmt)

    def _row_to_instance(self, row: RowMapping) -> Instance:
        return Instance(
            id=row["id"],
            name=row["name"],
            endpoint=row["endpoint"],
            connection=row["connection"],
            token_ref=row["token_ref"],
            tier=row["tier"],
            token_fingerprint=row["token_fingerprint"],
            token_hash=row["token_hash"],
            token_minted_at=row["token_minted_at"],
            schema_version=row["schema_version"],
            capabilities=json.loads(row["capabilities"]),
            health=row["health"],
            last_seen=row["last_seen"],
            created_at=row["created_at"],
        )

    def get(self, instance_id: str) -> Instance | None:
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(select(instances).where(instances.c.id == instance_id))
                .mappings()
                .first()
            )
        return self._row_to_instance(row) if row else None

    def list_all(self) -> list[Instance]:
        with self._lock, self._engine.connect() as conn:
            rows = conn.execute(select(instances).order_by(instances.c.created_at)).mappings().all()
        return [self._row_to_instance(r) for r in rows]

    def delete(self, instance_id: str) -> bool:
        with self._lock, self._engine.begin() as conn:
            result = conn.execute(delete(instances).where(instances.c.id == instance_id))
        return result.rowcount > 0

    def update_health(
        self,
        instance_id: str,
        *,
        health: Health,
        schema_version: str | None = None,
        capabilities: dict[str, object] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        values: dict[str, Any] = {"health": health, "last_seen": now}
        if schema_version is not None:
            values["schema_version"] = schema_version
        if capabilities is not None:
            values["capabilities"] = json.dumps(capabilities)
        with self._lock, self._engine.begin() as conn:
            conn.execute(update(instances).where(instances.c.id == instance_id).values(**values))

    def set_token(
        self,
        instance_id: str,
        *,
        token_ref: str,
        fingerprint: str,
        token_hash: str,
        tier: str,
        minted_at: str,
    ) -> None:
        """Record a freshly minted token's reference + hash + metadata — never the secret itself."""
        with self._lock, self._engine.begin() as conn:
            conn.execute(
                update(instances)
                .where(instances.c.id == instance_id)
                .values(
                    token_ref=token_ref,
                    token_fingerprint=fingerprint,
                    token_hash=token_hash,
                    tier=tier,
                    token_minted_at=minted_at,
                )
            )

    def get_by_token_hash(self, token_hash: str) -> Instance | None:
        """Find the instance whose minted connector→panel token has this hash (auth lookup)."""
        if not token_hash:
            return None
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(select(instances).where(instances.c.token_hash == token_hash))
                .mappings()
                .first()
            )
        return self._row_to_instance(row) if row else None

    # --- Mode B command queue -------------------------------------------------

    def _row_to_command(self, row: RowMapping) -> Command:
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
        with self._lock, self._engine.begin() as conn:
            conn.execute(
                commands.insert().values(
                    cmd_id=cmd.cmd_id,
                    instance_id=cmd.instance_id,
                    verb=cmd.verb,
                    args=json.dumps(cmd.args),
                    status="queued",
                    created_at=cmd.created_at,
                )
            )
        return cmd

    def claim_queued(self, instance_id: str, limit: int = 50) -> list[Command]:
        """Atomically move this instance's queued commands to 'dispatched' and return them.

        The claim (SELECT-then-UPDATE) must be atomic even across processes (multi-worker uvicorn)
        or two concurrent polls could claim the same commands → double dispatch. Dialect-aware:

        * **Postgres** — ``SELECT ... FOR UPDATE SKIP LOCKED`` locks the claimed rows and lets a
          concurrent claim skip past them (no blocking, no double-claim).
        * **SQLite** (no row locks) — escalate to a write transaction up front with
          ``BEGIN IMMEDIATE`` so a concurrent claim on another connection/process waits
          (busy_timeout) instead of racing. SQLAlchemy's implicit ``BEGIN`` is deferred and
          wouldn't take the write lock until the UPDATE — too late — so drive it in AUTOCOMMIT mode
          and issue the transaction control explicitly.
        """
        now = datetime.now(UTC).isoformat()
        base = (
            select(commands)
            .where(commands.c.instance_id == instance_id, commands.c.status == "queued")
            .order_by(commands.c.created_at)
            .limit(limit)
        )
        if self._engine.dialect.name == "postgresql":
            with self._lock, self._engine.begin() as conn:
                rows = conn.execute(base.with_for_update(skip_locked=True)).mappings().all()
                return self._dispatch(conn, rows, now)

        conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            with self._lock:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    rows = conn.execute(base).mappings().all()
                    cmds = self._dispatch(conn, rows, now)
                    conn.exec_driver_sql("COMMIT")
                    return cmds
                except Exception:
                    conn.exec_driver_sql("ROLLBACK")
                    raise
        finally:
            conn.close()

    def _dispatch(self, conn: Connection, rows: Sequence[RowMapping], now: str) -> list[Command]:
        """Mark the claimed rows dispatched and return them as commands."""
        cmds = [self._row_to_command(r) for r in rows]
        for cmd in cmds:
            conn.execute(
                update(commands)
                .where(commands.c.cmd_id == cmd.cmd_id)
                .values(status="dispatched", dispatched_at=now)
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
        with self._lock, self._engine.begin() as conn:
            row = (
                conn.execute(select(commands.c.status).where(commands.c.cmd_id == cmd_id))
                .mappings()
                .first()
            )
            if row is None:
                return False
            if row["status"] in ("done", "error"):
                return False  # at-least-once dedup: result already recorded
            conn.execute(
                update(commands)
                .where(commands.c.cmd_id == cmd_id)
                .values(
                    status=status,
                    output=json.dumps(output) if output is not None else None,
                    error=error,
                    result_at=now,
                )
            )
        return True

    def get_command(self, cmd_id: str) -> Command | None:
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(select(commands).where(commands.c.cmd_id == cmd_id)).mappings().first()
            )
        return self._row_to_command(row) if row else None

    def list_commands(self, instance_id: str, limit: int = 100) -> list[Command]:
        with self._lock, self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(commands)
                    .where(commands.c.instance_id == instance_id)
                    .order_by(commands.c.created_at.desc())
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        return [self._row_to_command(r) for r in rows]
