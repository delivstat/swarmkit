"""ProposalStore — the human-gated approval queue of the growth loop (design §17).

A proposal is a drafted artifact change (from a signal — gap / eval regression / drift — via the
authoring swarm) that must be **human-approved before it can publish or deploy**. The loop is
proposal automation, not autonomous self-modification: nothing here auto-approves. A proposal stays
``pending`` until a human explicitly approves (publishing it as a new registry version) or rejects.

Separation of powers (§8.7): approval/activation are reserved-for-human; the panel and the authoring
swarm (machines) can draft but never approve. The panel enforces this by allowing approve/reject
only for operator principals — connectors (machine tokens) are denied — and by there being **no
code path that transitions a proposal out of ``pending`` except the explicit approve/reject calls**.

Sqlite for now, mirroring the other stores.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from swarmkit_control_plane._sqlite_base import SqliteStore

Status = str  # "pending" | "approved" | "rejected"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    proposed_by TEXT NOT NULL DEFAULT '',
    signal TEXT NOT NULL DEFAULT '',
    eval_summary TEXT NOT NULL DEFAULT '{}',
    approved_by TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    published_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals (status, created_at);
"""


class ProposalStore(SqliteStore):
    """Thread-safe sqlite store for the approval queue."""

    _SCHEMA = _SCHEMA

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["content"] = json.loads(out["content"]) if out["content"] else None
        out["eval_summary"] = json.loads(out["eval_summary"]) if out["eval_summary"] else {}
        return out

    def create(
        self,
        *,
        kind: str,
        artifact_id: str,
        content: Any,
        proposed_by: str = "",
        signal: str = "",
        eval_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Open a proposal — always starts ``pending`` (never auto-approved)."""
        pid = uuid4().hex[:12]
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO proposals
                   (id, kind, artifact_id, content, status, proposed_by, signal, eval_summary,
                    created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    pid,
                    kind,
                    artifact_id,
                    json.dumps(content),
                    proposed_by,
                    signal,
                    json.dumps(eval_summary or {}),
                    now,
                ),
            )
            return self._get(conn, pid)

    def _get(self, conn: sqlite3.Connection, pid: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
        if row is None:
            raise KeyError(pid)
        return self._row(row)

    def get(self, pid: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
        return self._row(row) if row else None

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM proposals WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM proposals ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def _decide(
        self, pid: str, *, status: str, approved_by: str, reason: str, published_version: str
    ) -> dict[str, Any]:
        """Transition a pending proposal to approved/rejected. Raises if it isn't pending."""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            current = conn.execute("SELECT status FROM proposals WHERE id = ?", (pid,)).fetchone()
            if current is None:
                raise KeyError(pid)
            if current["status"] != "pending":
                raise ValueError(f"proposal is {current['status']}, not pending")
            conn.execute(
                """UPDATE proposals SET status = ?, approved_by = ?, reason = ?,
                   published_version = ?, decided_at = ? WHERE id = ?""",
                (status, approved_by, reason, published_version, now, pid),
            )
            return self._get(conn, pid)

    def mark_approved(
        self, pid: str, *, approved_by: str, published_version: str
    ) -> dict[str, Any]:
        return self._decide(
            pid,
            status="approved",
            approved_by=approved_by,
            reason="",
            published_version=published_version,
        )

    def mark_rejected(self, pid: str, *, approved_by: str, reason: str) -> dict[str, Any]:
        return self._decide(
            pid, status="rejected", approved_by=approved_by, reason=reason, published_version=""
        )

    def set_published_version(self, pid: str, version: str) -> dict[str, Any]:
        """Backfill the registry version an approved proposal published.

        Approval claims the proposal (pending→approved) *before* it publishes, so that two
        concurrent approvals can't both publish — only the claim winner reaches the registry.
        The version isn't known until that publish, so it's recorded here in a second step.
        See ``GrowthService.approve``.
        """
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE proposals SET published_version = ? WHERE id = ?", (version, pid)
            )
            if cur.rowcount == 0:
                raise KeyError(pid)
            return self._get(conn, pid)
