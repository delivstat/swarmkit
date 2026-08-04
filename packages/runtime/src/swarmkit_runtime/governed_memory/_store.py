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

from swarmkit_runtime.governed_memory._decay import DecayConfig, effective_confidence
from swarmkit_runtime.governed_memory._models import (
    ChangeLogEntry,
    Memory,
    MemoryCandidate,
    QuarantineItem,
    WriteOutcome,
    content_hash,
    memory_key,
)
from swarmkit_runtime.governed_memory._reconcile import (
    Reconciler,
    ReconcileRequest,
    reconcile,
)
from swarmkit_runtime.governed_memory._relevance import (
    Embedder,
    embedding_scores,
    lexical_scores,
)
from swarmkit_runtime.governed_memory._tables import change_log, memory, metadata, quarantine
from swarmkit_runtime.persistence._store import create_all_idempotent

_CONFIDENCE_CEILING = 1.0
_REINFORCE_STEP = 0.05


class GovernedMemoryStore:
    """Governed memory: reconcile-on-write, update-in-place, append-only change-log."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] | None = None,
        reconciler: Reconciler | None = None,
        decay: DecayConfig | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._reconciler = reconciler
        self._decay = decay
        self._embedder = embedder
        create_all_idempotent(metadata, engine)

    @classmethod
    def for_workspace(
        cls,
        workspace_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        reconciler: Reconciler | None = None,
        decay: DecayConfig | None = None,
        embedder: Embedder | None = None,
    ) -> GovernedMemoryStore:
        """The governed-memory store on the workspace's CONFIGURED backend.

        Was hardcoded to ``{workspace}/.swarmkit/store.sqlite``, which is why a Postgres workspace
        accumulated its memory in a local file nothing else could read — the one part of bug 01
        that loses knowledge rather than just hiding it.
        """
        from swarmkit_runtime.persistence import StoreKind, storage_for_workspace  # noqa: PLC0415

        return cls(
            storage_for_workspace(workspace_path).engine(StoreKind.MEMORY),
            clock=clock,
            reconciler=reconciler,
            decay=decay,
            embedder=embedder,
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    # ── write path ────────────────────────────────────────────────────────────────────────────
    def write(self, candidate: MemoryCandidate) -> WriteOutcome:
        """Govern a candidate through the *deterministic* reconcile and apply it (no LLM).

        ``new`` inserts; ``reinforce`` bumps recency/confidence (no value change, no new row);
        ``update`` supersedes the value in place. A changed value always ``update``s here — for
        refine/contradict discrimination use :meth:`awrite` with a reconciler wired.
        """
        with self._engine.begin() as conn:
            current = self._get_locked(conn, candidate.key)
            decision = reconcile(candidate, current)
            now = self._clock().isoformat()
            if decision.op == "new":
                return self._apply_new(conn, candidate, now)
            assert current is not None
            if decision.op == "reinforce":
                return self._apply_reinforce(conn, current, now)
            return self._apply_update(conn, current, candidate, now, reason="changed value")

    async def awrite(self, candidate: MemoryCandidate) -> WriteOutcome:
        """Govern a candidate through the deterministic reconcile *and*, on a changed value, the
        reconcile decision skill (design/details/governed-memory.md).

        ``new`` / ``reinforce`` resolve deterministically (no LLM). A changed value invokes the
        wired reconciler, which returns ``update`` (supersede), ``refine`` (apply the merged value),
        or ``contradict`` (leave the trusted memory untouched, quarantine the candidate + escalate).
        With no reconciler wired it falls back to a deterministic ``update`` (as :meth:`write`).
        """
        # Read the current snapshot (short txn), judge outside any lock, then apply (fresh txn).
        current = self._read(candidate.key)
        decision = reconcile(candidate, current)
        if decision.op != "update" or self._reconciler is None:
            return self.write(candidate)  # new / reinforce / no-judge update — deterministic

        assert current is not None
        verdict = await self._reconciler(ReconcileRequest(candidate=candidate, current=current))
        with self._engine.begin() as conn:
            live = self._get_locked(conn, candidate.key)  # re-read under the write txn
            if live is None:  # raced away — treat as a fresh write
                return self._apply_new(conn, candidate, self._clock().isoformat())
            now = self._clock().isoformat()
            if verdict.op == "contradict":
                return self._apply_contradict(conn, live, candidate, now, verdict.reasoning)
            if verdict.op == "refine":
                merged = (
                    verdict.merged_value if verdict.merged_value is not None else candidate.value
                )
                return self._apply_update(
                    conn,
                    live,
                    candidate,
                    now,
                    op="refine",
                    value=merged,
                    reason=verdict.reasoning or "refined into existing memory",
                    decided_by="skill",
                )
            return self._apply_update(
                conn,
                live,
                candidate,
                now,
                reason=verdict.reasoning or "changed value",
                decided_by="skill",
            )

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
        """Retrieve active memories, ranked.

        With a ``query``: relevance-ranked (cosine similarity when an embedder is wired, else a
        local TF-IDF lexical score), with *effective* confidence as the secondary signal; only
        memories relevant to the query are returned. With ``query=""``: all, by effective confidence
        (decayed by recency when a :class:`DecayConfig` is wired) then recency."""
        stmt = select(memory)
        if not include_inactive:
            stmt = stmt.where(memory.c.status == "active")
        if types:
            stmt = stmt.where(memory.c.type.in_(types))
        with self._engine.connect() as conn:
            mems = [self._row_to_memory(dict(r)) for r in conn.execute(stmt).mappings()]
        now = self._clock()

        q = query.strip()
        if not q:
            mems.sort(
                key=lambda m: (self._rank_confidence(m, now), m.last_reinforced_at), reverse=True
            )
            return mems[:limit]

        docs = [self._searchable(m) for m in mems]
        scores = (
            embedding_scores(q, docs, self._embedder)
            if self._embedder is not None
            else lexical_scores(q, docs)
        )
        ranked = [(m, s) for m, s in zip(mems, scores, strict=True) if s > 0]
        ranked.sort(key=lambda ms: (ms[1], self._rank_confidence(ms[0], now)), reverse=True)
        return [m for m, _ in ranked[:limit]]

    def _rank_confidence(self, m: Memory, now: datetime) -> float:
        return effective_confidence(m, now, self._decay) if self._decay else m.confidence

    @staticmethod
    def _searchable(m: Memory) -> str:
        return f"{m.subject} {m.attribute} {m.value}"

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

    # ── quarantine (the curator's contradiction queue) ──────────────────────────────────────────
    def list_quarantine(self, *, status: str = "pending") -> list[QuarantineItem]:
        """Parked contradictions with the given ``status`` (default ``pending``), oldest first."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(quarantine)
                .where(quarantine.c.status == status)
                .order_by(quarantine.c.id.asc())
            ).mappings()
            return [self._row_to_quarantine(dict(r)) for r in rows]

    def resolve_quarantine(
        self, quarantine_id: int, *, accept: bool, resolved_by: str
    ) -> WriteOutcome | None:
        """The curator's decision on a parked contradiction (design §8: the one hard human gate).

        ``accept`` applies the quarantined candidate as an ``update`` to the canonical memory (the
        curator affirms the new value supersedes); reject discards it. Either way the item is marked
        resolved (append-only status transition) — returns the resulting :class:`WriteOutcome` on
        accept, ``None`` on reject or an already-resolved / unknown id.
        """
        with self._engine.begin() as conn:
            item = (
                conn.execute(
                    select(quarantine).where(
                        quarantine.c.id == quarantine_id, quarantine.c.status == "pending"
                    )
                )
                .mappings()
                .first()
            )
            if item is None:
                return None
            now = self._clock().isoformat()
            conn.execute(
                update(quarantine)
                .where(quarantine.c.id == quarantine_id)
                .values(
                    status="accepted" if accept else "rejected",
                    resolved_at=now,
                    resolved_by=resolved_by,
                )
            )
            if not accept:
                return None
            candidate = _dict_to_candidate(json.loads(item["candidate"]))
            current = self._get_locked(conn, candidate.key)
            if current is None:
                return self._apply_new(conn, candidate, now)
            return self._apply_update(
                conn,
                current,
                candidate,
                now,
                reason=f"curator accepted quarantined change ({resolved_by})",
                decided_by="curator",
            )

    # ── apply helpers (called inside an open write txn) ─────────────────────────────────────────
    def _apply_new(self, conn: Any, candidate: MemoryCandidate, now: str) -> WriteOutcome:
        row = self._new_row(candidate, now)  # provenance as a dict
        conn.execute(insert(memory).values(key=row["key"], **_updatable(row)))
        self._log(conn, candidate.key, "new", None, row, now, reason="new key")
        return WriteOutcome("new", candidate.key, self._row_to_memory(row), changed=True)

    def _apply_reinforce(self, conn: Any, current: Memory, now: str) -> WriteOutcome:
        before = self._memory_to_dict(current)
        after = {
            **before,
            "confidence": min(_CONFIDENCE_CEILING, current.confidence + _REINFORCE_STEP),
            "last_reinforced_at": now,
            "reinforce_count": current.reinforce_count + 1,
        }
        conn.execute(update(memory).where(memory.c.key == current.key).values(**_updatable(after)))
        self._log(conn, current.key, "reinforce", before, after, now, reason="identical value")
        return WriteOutcome("reinforce", current.key, self._row_to_memory(after), changed=False)

    def _apply_update(
        self,
        conn: Any,
        current: Memory,
        candidate: MemoryCandidate,
        now: str,
        *,
        op: str = "update",
        value: str | None = None,
        reason: str,
        decided_by: str = "deterministic",
    ) -> WriteOutcome:
        """Supersede the value in place (``update``) or apply a merged value (``refine``);
        ``valid_from`` stays (first appearance of the key). Logs ``op`` with ``decided_by``."""
        new_value = value if value is not None else candidate.value
        before = self._memory_to_dict(current)
        after = {
            **before,
            "value": new_value,
            "content_hash": content_hash(new_value),
            "type": candidate.type,
            "confidence": candidate.confidence,
            "last_reinforced_at": now,
            "reinforce_count": 1,
            "source": candidate.source,
            "provenance": candidate.provenance,
        }
        conn.execute(update(memory).where(memory.c.key == current.key).values(**_updatable(after)))
        self._log(conn, current.key, op, before, after, now, reason=reason, decided_by=decided_by)
        return WriteOutcome(op, current.key, self._row_to_memory(after), changed=True)  # type: ignore[arg-type]

    def _apply_contradict(
        self, conn: Any, current: Memory, candidate: MemoryCandidate, now: str, reasoning: str
    ) -> WriteOutcome:
        """A contradiction: leave the trusted canonical memory untouched, park the candidate on the
        quarantine queue for the curator, and log the (rejected-for-now) contradiction."""
        conn.execute(
            insert(quarantine).values(
                memory_key=current.key,
                candidate=json.dumps(_candidate_dict(candidate)),
                current_value=current.value,
                reasoning=reasoning,
                status="pending",
                created_at=now,
            )
        )
        before = self._memory_to_dict(current)
        after = {**before, "value": candidate.value, "content_hash": content_hash(candidate.value)}
        self._log(
            conn,
            current.key,
            "contradict",
            before,
            after,
            now,
            reason=reasoning or "contradicts trusted memory",
            decided_by="skill",
        )
        return WriteOutcome("contradict", current.key, current, changed=False)

    # ── internals ─────────────────────────────────────────────────────────────────────────────
    def _read(self, key: str) -> Memory | None:
        with self._engine.connect() as conn:
            return self._get_locked(conn, key)

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
        decided_by: str = "deterministic",
    ) -> None:
        conn.execute(
            insert(change_log).values(
                memory_key=key,
                op=op,
                before=json.dumps(before) if before is not None else None,
                after=json.dumps(after),
                reason=reason,
                decided_by=decided_by,
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

    @staticmethod
    def _row_to_quarantine(row: RowMapping | dict[str, Any]) -> QuarantineItem:
        return QuarantineItem(
            id=int(row["id"]),
            memory_key=row["memory_key"],
            candidate=json.loads(row["candidate"]),
            current_value=row["current_value"],
            reasoning=row["reasoning"],
            status=row["status"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"],
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


def _candidate_dict(c: MemoryCandidate) -> dict[str, Any]:
    """A MemoryCandidate as a plain dict (for the quarantine ``candidate`` json column)."""
    return {
        "subject": c.subject,
        "attribute": c.attribute,
        "value": c.value,
        "type": c.type,
        "confidence": c.confidence,
        "source": c.source,
        "provenance": c.provenance,
    }


def _dict_to_candidate(d: dict[str, Any]) -> MemoryCandidate:
    return MemoryCandidate(
        subject=d["subject"],
        attribute=d["attribute"],
        value=d["value"],
        type=d.get("type", "semantic"),
        confidence=float(d.get("confidence", 1.0)),
        source=d.get("source"),
        provenance=d.get("provenance") or {},
    )
