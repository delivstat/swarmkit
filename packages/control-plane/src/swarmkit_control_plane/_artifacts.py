"""ArtifactStore — central versioned registry for SwarmKit artifacts + drift detection.

Versions topologies / skills / archetypes / workspace / triggers with provenance and a
``content_hash`` (design details/control-plane/15-artifact-registry.md). Registering identical
content is idempotent (returns the existing latest); changed content is a new version, never a
silent overwrite. Tracks the registry-*intended* version per instance (a deployment) and the
instance's *reported* actual version, and computes drift between them.

Sqlite for now, mirroring the registry/aggregation stores; the design's git-backed content store +
Postgres metadata is a later swap. The governed/audited/human-gated push to instances, the
schema-compatibility gate, and the UI surface are separate slices.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KINDS = ("topology", "skill", "archetype", "workspace", "trigger")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_versions (
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content TEXT NOT NULL,
    authored_by TEXT NOT NULL DEFAULT '',
    schema_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (kind, id, version)
);
CREATE TABLE IF NOT EXISTS deployments (
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, kind, id)
);
CREATE TABLE IF NOT EXISTS reported_artifacts (
    instance_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    id TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    reported_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, kind, id)
);
"""


def content_hash(content: Any) -> str:
    """Stable SHA-256 of artifact content (dicts hashed canonically, sorted keys)."""
    text = (
        json.dumps(content, sort_keys=True, separators=(",", ":"))
        if isinstance(content, dict)
        else str(content)
    )
    return hashlib.sha256(text.encode()).hexdigest()


class ArtifactStore:
    """Thread-safe sqlite store for versioned artifacts, deployments, and drift."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL lets connector pushes and operator reads proceed concurrently without
        # blocking; busy_timeout retries under contention instead of raising "locked".
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def register_version(
        self,
        kind: str,
        id: str,
        *,
        content: Any,
        authored_by: str = "",
        schema_version: str = "",
        version: str | None = None,
    ) -> dict[str, Any]:
        """Register a version. Idempotent: identical content to the latest returns that version."""
        chash = content_hash(content)
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT version, content_hash, seq FROM artifact_versions "
                "WHERE kind = ? AND id = ? ORDER BY seq DESC",
                (kind, id),
            ).fetchall()
            if rows and rows[0]["content_hash"] == chash:
                return self._get_version(conn, kind, id, rows[0]["version"])  # no-op
            seq = (rows[0]["seq"] + 1) if rows else 1
            ver = version or f"v{seq}"
            conn.execute(
                """INSERT INTO artifact_versions
                   (kind, id, version, content_hash, content, authored_by, schema_version,
                    created_at, seq)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    kind,
                    id,
                    ver,
                    chash,
                    json.dumps(content) if isinstance(content, dict) else str(content),
                    authored_by,
                    schema_version,
                    now,
                    seq,
                ),
            )
            return self._get_version(conn, kind, id, ver)

    def _get_version(
        self, conn: sqlite3.Connection, kind: str, id: str, version: str
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM artifact_versions WHERE kind = ? AND id = ? AND version = ?",
            (kind, id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"{kind}/{id}@{version}")
        return self._row_to_version(row)

    def _row_to_version(self, row: sqlite3.Row, *, with_content: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": row["kind"],
            "id": row["id"],
            "version": row["version"],
            "content_hash": row["content_hash"],
            "authored_by": row["authored_by"],
            "schema_version": row["schema_version"],
            "created_at": row["created_at"],
        }
        if with_content:
            raw = row["content"]
            try:
                out["content"] = json.loads(raw)
            except (ValueError, TypeError):
                out["content"] = raw
        return out

    def get_version(self, kind: str, id: str, version: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_versions WHERE kind = ? AND id = ? AND version = ?",
                (kind, id, version),
            ).fetchone()
        return self._row_to_version(row) if row else None

    def list_versions(self, kind: str, id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_versions WHERE kind = ? AND id = ? ORDER BY seq DESC",
                (kind, id),
            ).fetchall()
        return [self._row_to_version(r, with_content=False) for r in rows]

    def list_artifacts(self) -> list[dict[str, Any]]:
        """One row per (kind, id): latest version + total version count."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT kind, id, COUNT(*) AS versions, MAX(seq) AS max_seq
                   FROM artifact_versions GROUP BY kind, id ORDER BY kind, id""",
            ).fetchall()
            out = []
            for r in rows:
                latest = conn.execute(
                    "SELECT version, content_hash FROM artifact_versions "
                    "WHERE kind = ? AND id = ? AND seq = ?",
                    (r["kind"], r["id"], r["max_seq"]),
                ).fetchone()
                out.append(
                    {
                        "kind": r["kind"],
                        "id": r["id"],
                        "versions": r["versions"],
                        "latest_version": latest["version"],
                        "latest_hash": latest["content_hash"],
                    }
                )
        return out

    # --- deployments (registry-intended version per instance) ----------------

    def set_deployment(self, instance_id: str, kind: str, id: str, version: str) -> None:
        with self._lock, self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM artifact_versions WHERE kind = ? AND id = ? AND version = ?",
                (kind, id, version),
            ).fetchone()
            if exists is None:
                raise KeyError(f"no such version {kind}/{id}@{version}")
            conn.execute(
                """INSERT OR REPLACE INTO deployments (instance_id, kind, id, version, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (instance_id, kind, id, version, datetime.now(UTC).isoformat()),
            )

    def list_deployments(self, instance_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT kind, id, version, updated_at FROM deployments WHERE instance_id = ? "
                "ORDER BY kind, id",
                (instance_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- reported actual + drift ---------------------------------------------

    def report(self, instance_id: str, records: list[dict[str, Any]]) -> int:
        """Record an instance's actual active artifact versions (for drift)."""
        now = datetime.now(UTC).isoformat()
        n = 0
        with self._lock, self._connect() as conn:
            for rec in records:
                kind, rid = rec.get("kind"), rec.get("id")
                if kind not in KINDS or not rid:
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO reported_artifacts
                       (instance_id, kind, id, version, content_hash, reported_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        instance_id,
                        kind,
                        rid,
                        str(rec.get("version", "")),
                        str(rec.get("content_hash", "")),
                        now,
                    ),
                )
                n += 1
        return n

    def drift(self, instance_id: str) -> list[dict[str, Any]]:
        """Compare each intended deployment against the instance's reported actual."""
        with self._lock, self._connect() as conn:
            intended = conn.execute(
                "SELECT kind, id, version FROM deployments WHERE instance_id = ?",
                (instance_id,),
            ).fetchall()
            out = []
            for d in intended:
                want = conn.execute(
                    "SELECT content_hash FROM artifact_versions WHERE kind = ? AND id = ? "
                    "AND version = ?",
                    (d["kind"], d["id"], d["version"]),
                ).fetchone()
                got = conn.execute(
                    "SELECT version, content_hash FROM reported_artifacts "
                    "WHERE instance_id = ? AND kind = ? AND id = ?",
                    (instance_id, d["kind"], d["id"]),
                ).fetchone()
                if got is None:
                    status = "missing"  # intended but the instance hasn't reported it
                elif got["version"] == d["version"] and (
                    not want
                    or not got["content_hash"]
                    or got["content_hash"] == want["content_hash"]
                ):
                    status = "ok"
                else:
                    status = "drift"
                out.append(
                    {
                        "kind": d["kind"],
                        "id": d["id"],
                        "intended_version": d["version"],
                        "actual_version": got["version"] if got else None,
                        "status": status,
                    }
                )
        return out
