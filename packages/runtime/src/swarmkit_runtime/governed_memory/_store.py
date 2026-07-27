"""GovernedMemoryStore — governed memory writes with temporal update-in-place
(design/details/governed-memory.md).

A ``persistence``-tier store over SQLAlchemy Core (SQLite default, Postgres via the same seam as
the core persistence store). Every ``write`` runs the deterministic reconcile (``_reconcile``) and
lands as an update-in-place on the canonical ``governed_memory`` table plus an append-only entry on
``governed_memory_change_log``. Reads are recency/confidence-weighted; ``value_as_of`` reconstructs
a fact at a past time from the change-log.

Named ``GovernedMemoryStore`` to avoid colliding with the existing conversation-recall
``MemoryStore`` (``memory/_store.py``, JSON + TF-IDF) — a different memory tier. Sync, mirroring
``persistence.Store``; an injectable ``clock`` keeps the temporal behaviour testable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import RowMapping

from swarmkit_runtime.governed_memory._models import (
    ChangeLogEntry,
    Memory,
    MemoryCandidate,
    WriteOutcome,
    content_hash,
    memory_key,
)
from swarmkit_runtime.governed_memory._reconcile import reconcile
from swarmkit_runtime.governed_memory._tables import change_log, memory, metadata
from swarmkit_runtime.persistence._store import make_engine

_CONFIDENCE_CEILING = 1.0
_REINFORCE_STEP = 0.05


class GovernedMemoryStore:
    """Governed memory: reconcile-on-write, update-in-place, append-only change-log."""

    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        metadata.create_all(engine)

    @classmethod
    def for_workspace(
        cls, workspace_path: str | Path, *, clock: Callable[[], datetime] | None = None
    ) -> GovernedMemoryStore:
        """Back-compat convenience: point the store at ``{workspace}/.swarmkit/store.sqlite`` —
        the same DB file the core persistence store uses (coexisting tables)."""
        db = Path(workspace_path) / ".swarmkit" / "store.sqlite"
        return cls(make_engine(f"sqlite:///{db}"), clock=clock)

    @property
    def engine(self) -> Engine:
        return self._engine

    # ── write path ────────────────────────────────────────────────────────────────────────────
    def write(self, candidate: MemoryCandidate) -> WriteOutcome:
        """Govern a candidate through the deterministic reconcile and apply it.

        ``new`` inserts; ``reinforce`` bumps recency/confidence (no value change, no new row);
        ``update`` supersedes the value in place. Every op appends a change-log entry.
        """
        key = candidate.key
        with self._engine.begin() as conn:
            current = self._get_locked(conn, key)
            decision = reconcile(candidate, current)
            now = self._clock().isoformat()

            if decision.op == "new":
                row = self._new_row(candidate, now)  # provenance as a dict
                conn.execute(insert(memory).values(key=row["key"], **_updatable(row)))
                self._log(conn, key, "new", None, row, now, reason="new key")
                return WriteOutcome("new", key, self._row_to_memory(row), changed=True)

            assert current is not None
            before = self._memory_to_dict(current)
            if decision.op == "reinforce":
                after = {
                    **before,
                    "confidence": min(_CONFIDENCE_CEILING, current.confidence + _REINFORCE_STEP),
                    "last_reinforced_at": now,
                    "reinforce_count": current.reinforce_count + 1,
                }
                conn.execute(update(memory).where(memory.c.key == key).values(**_updatable(after)))
                self._log(conn, key, "reinforce", before, after, now, reason="identical value")
                return WriteOutcome("reinforce", key, self._row_to_memory(after), changed=False)

            # update — supersede the value in place; valid_from stays (first appearance of the key).
            after = {
                **before,
                "value": candidate.value,
                "content_hash": content_hash(candidate.value),
                "type": candidate.type,
                "confidence": candidate.confidence,
                "last_reinforced_at": now,
                "reinforce_count": 1,
                "source": candidate.source,
                "provenance": candidate.provenance,
            }
            conn.execute(update(memory).where(memory.c.key == key).values(**_updatable(after)))
            self._log(conn, key, "update", before, after, now, reason="changed value")
            return WriteOutcome("update", key, self._row_to_memory(after), changed=True)

    # ── reads ─────────────────────────────────────────────────────────────────────────────────
    def get(self, subject: str, attribute: str) -> Memory | None:
        """The current memory for a ``(subject, attribute)`` key, or None."""
        with self._engine.connect() as conn:
            row = (
                conn.execute(select(memory).where(memory.c.key == memory_key(subject, attribute)))
                .mappings()
                .first()
            )
        return self._row_to_memory(dict(row)) if row else None

    def search(
        self,
        query: str = "",
        *,
        types: list[str] | None = None,
        limit: int = 20,
        include_inactive: bool = False,
    ) -> list[Memory]:
        """Active memories matching ``query`` (case-insensitive substring over subject/attribute/
        value), ranked by confidence then recency. Semantic search is a later slice — this is the
        deterministic scan; ``query=""`` returns all (ranked)."""
        stmt = select(memory)
        if not include_inactive:
            stmt = stmt.where(memory.c.status == "active")
        if types:
            stmt = stmt.where(memory.c.type.in_(types))
        with self._engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(stmt).mappings()]
        q = query.strip().lower()
        if q:
            rows = [r for r in rows if q in f"{r['subject']} {r['attribute']} {r['value']}".lower()]
        rows.sort(key=lambda r: (r["confidence"], r["last_reinforced_at"]), reverse=True)
        return [self._row_to_memory(r) for r in rows[:limit]]

    def history(self, subject: str, attribute: str) -> list[ChangeLogEntry]:
        """The append-only change timeline for a key, oldest first — the fact's full history."""
        key = memory_key(subject, attribute)
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(change_log)
                .where(change_log.c.memory_key == key)
                .order_by(change_log.c.id.asc())
            ).mappings()
            return [self._row_to_change(dict(r)) for r in rows]

    def value_as_of(self, subject: str, attribute: str, as_of: datetime) -> Memory | None:
        """Reconstruct a fact as it stood at ``as_of`` — the after-state of the latest change at or
        before that time — from the append-only log. None if the key didn't exist yet."""
        key = memory_key(subject, attribute)
        cutoff = as_of.isoformat()
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(change_log)
                    .where(change_log.c.memory_key == key, change_log.c.timestamp <= cutoff)
                    .order_by(change_log.c.id.desc())
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return self._row_to_memory(json.loads(row["after"]))

    # ── internals ─────────────────────────────────────────────────────────────────────────────
    def _get_locked(self, conn: Any, key: str) -> Memory | None:
        row = conn.execute(select(memory).where(memory.c.key == key)).mappings().first()
        return self._row_to_memory(dict(row)) if row else None

    def _new_row(self, candidate: MemoryCandidate, now: str) -> dict[str, Any]:
        return {
            "key": candidate.key,
            "subject": candidate.subject,
            "attribute": candidate.attribute,
            "value": candidate.value,
            "type": candidate.type,
            "confidence": candidate.confidence,
            "content_hash": content_hash(candidate.value),
            "valid_from": now,
            "last_reinforced_at": now,
            "reinforce_count": 1,
            "source": candidate.source,
            "provenance": candidate.provenance,
            "status": "active",
        }

    def _log(
        self,
        conn: Any,
        key: str,
        op: str,
        before: dict[str, Any] | None,
        after: dict[str, Any],
        now: str,
        *,
        reason: str,
    ) -> None:
        conn.execute(
            insert(change_log).values(
                memory_key=key,
                op=op,
                before=json.dumps(before) if before is not None else None,
                after=json.dumps(after),
                reason=reason,
                decided_by="deterministic",
                timestamp=now,
            )
        )

    @staticmethod
    def _memory_to_dict(m: Memory) -> dict[str, Any]:
        return {
            "key": m.key,
            "subject": m.subject,
            "attribute": m.attribute,
            "value": m.value,
            "type": m.type,
            "confidence": m.confidence,
            "content_hash": m.content_hash,
            "valid_from": m.valid_from,
            "last_reinforced_at": m.last_reinforced_at,
            "reinforce_count": m.reinforce_count,
            "source": m.source,
            "provenance": m.provenance,
            "status": m.status,
        }

    @staticmethod
    def _row_to_memory(row: RowMapping | dict[str, Any]) -> Memory:
        prov = row["provenance"]
        return Memory(
            key=row["key"],
            subject=row["subject"],
            attribute=row["attribute"],
            value=row["value"],
            type=row["type"],
            confidence=float(row["confidence"]),
            content_hash=row["content_hash"],
            valid_from=row["valid_from"],
            last_reinforced_at=row["last_reinforced_at"],
            reinforce_count=int(row["reinforce_count"]),
            source=row["source"],
            provenance=json.loads(prov) if isinstance(prov, str) else dict(prov or {}),
            status=row["status"],
        )

    @staticmethod
    def _row_to_change(row: RowMapping | dict[str, Any]) -> ChangeLogEntry:
        return ChangeLogEntry(
            id=int(row["id"]),
            memory_key=row["memory_key"],
            op=row["op"],
            before=json.loads(row["before"]) if row["before"] else None,
            after=json.loads(row["after"]),
            reason=row["reason"],
            decided_by=row["decided_by"],
            timestamp=row["timestamp"],
        )


def _updatable(after: dict[str, Any]) -> dict[str, Any]:
    """The mutable columns of a memory row (everything but the immutable key), with JSON-encoded
    provenance — the value shape SQLAlchemy ``update`` wants."""
    row = {k: v for k, v in after.items() if k != "key"}
    row["provenance"] = (
        json.dumps(row["provenance"])
        if not isinstance(row["provenance"], str)
        else row["provenance"]
    )
    return row
