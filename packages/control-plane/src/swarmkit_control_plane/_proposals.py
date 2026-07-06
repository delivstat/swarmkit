"""ProposalStore — the human-gated approval queue of the growth loop (design §17).

A proposal is a drafted artifact change (from a signal — gap / eval regression / drift — via the
authoring swarm) that must be **human-approved before it can publish or deploy**. The loop is
proposal automation, not autonomous self-modification: nothing here auto-approves. A proposal stays
``pending`` until a human explicitly approves (publishing it as a new registry version) or rejects.

Separation of powers (§8.7): approval/activation are reserved-for-human; the panel and the authoring
swarm (machines) can draft but never approve. The panel enforces this by allowing approve/reject
only for operator principals — connectors (machine tokens) are denied — and by there being **no
code path that transitions a proposal out of ``pending`` except the explicit approve/reject calls**.

SQLAlchemy Core over SQLite (default) or Postgres (design/details/postgres-backend.md).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Connection, RowMapping

from swarmkit_control_plane._store_base import Store
from swarmkit_control_plane._tables import proposals

Status = str  # "pending" | "approved" | "rejected"


class ProposalStore(Store):
    """Thread-safe store for the approval queue."""

    def _row(self, row: RowMapping) -> dict[str, Any]:
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
        with self._lock, self._engine.begin() as conn:
            conn.execute(
                proposals.insert().values(
                    id=pid,
                    kind=kind,
                    artifact_id=artifact_id,
                    content=json.dumps(content),
                    status="pending",
                    proposed_by=proposed_by,
                    signal=signal,
                    eval_summary=json.dumps(eval_summary or {}),
                    created_at=now,
                )
            )
            return self._get(conn, pid)

    def _get(self, conn: Connection, pid: str) -> dict[str, Any]:
        row = conn.execute(select(proposals).where(proposals.c.id == pid)).mappings().first()
        if row is None:
            raise KeyError(pid)
        return self._row(row)

    def get(self, pid: str) -> dict[str, Any] | None:
        with self._lock, self._engine.connect() as conn:
            row = conn.execute(select(proposals).where(proposals.c.id == pid)).mappings().first()
        return self._row(row) if row else None

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        stmt = select(proposals).order_by(proposals.c.created_at.desc())
        if status:
            stmt = stmt.where(proposals.c.status == status)
        with self._lock, self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._row(r) for r in rows]

    def _decide(
        self, pid: str, *, status: str, approved_by: str, reason: str, published_version: str
    ) -> dict[str, Any]:
        """Transition a pending proposal to approved/rejected. Raises if it isn't pending.

        The status read takes a row lock (``FOR UPDATE`` on Postgres; a no-op on SQLite, which
        serialises writers via the store lock + WAL) so two concurrent decisions can't both pass the
        pending check.
        """
        now = datetime.now(UTC).isoformat()
        with self._lock, self._engine.begin() as conn:
            current = (
                conn.execute(
                    select(proposals.c.status).where(proposals.c.id == pid).with_for_update()
                )
                .mappings()
                .first()
            )
            if current is None:
                raise KeyError(pid)
            if current["status"] != "pending":
                raise ValueError(f"proposal is {current['status']}, not pending")
            conn.execute(
                update(proposals)
                .where(proposals.c.id == pid)
                .values(
                    status=status,
                    approved_by=approved_by,
                    reason=reason,
                    published_version=published_version,
                    decided_at=now,
                )
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
        with self._lock, self._engine.begin() as conn:
            cur = conn.execute(
                update(proposals).where(proposals.c.id == pid).values(published_version=version)
            )
            if cur.rowcount == 0:
                raise KeyError(pid)
            return self._get(conn, pid)
