"""Workspace memory store — persistent knowledge that grows with use.

Stores structured insights extracted from conversations, on the runtime's persistence layer. Each
entry captures a topic, context, key points, and links to related sessions. Lexical search, local,
no API keys.

See design/details/workspace-memory.md.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, delete, func, insert, or_, select

from swarmkit_runtime.memory._tables import metadata, workspace_memory
from swarmkit_runtime.persistence._store import redacted_url

logger = logging.getLogger("swarmkit.memory")


@dataclass
class MemoryEntry:
    """A single memory node in the workspace knowledge graph."""

    id: str
    user: str | None = None
    session_id: str | None = None
    topic: str = ""
    context: str = ""
    key_points: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related_sessions: list[str] = field(default_factory=list)
    created_at: str = ""
    source_agent: str | None = None

    @property
    def searchable_text(self) -> str:
        parts = [self.topic, self.context]
        parts.extend(self.key_points)
        parts.extend(self.tags)
        return " ".join(parts).lower()


class MemoryStore:
    """Workspace memory on the runtime's persistence layer, with lexical search.

    Rows live in the ``workspace_memory`` table of the ``memory`` store kind — resolved by the
    storage service from ``storage.runtime`` (SQLite by default, Postgres when configured). It used
    to be ``{workspace}/.swarmkit/memory.json``: a file the storage service could not move, so a
    Postgres workspace had its memory on local disk. A legacy file is imported on first open and
    renamed ``memory.json.migrated``.

    Parameters
    ----------
    workspace_path:
        Root of the workspace directory (where ``workspace.yaml`` and the storage config live).
    engine:
        An engine to use instead of the resolved one — for tests and the migrator.
    """

    def __init__(self, workspace_path: Path, *, engine: Engine | None = None) -> None:
        from swarmkit_runtime.persistence import StoreKind, storage_for_workspace  # noqa: PLC0415
        from swarmkit_runtime.persistence._store import create_all_idempotent  # noqa: PLC0415

        self._root = Path(workspace_path)
        self._engine = (
            engine
            if engine is not None
            else storage_for_workspace(self._root).engine(StoreKind.MEMORY)
        )
        create_all_idempotent(metadata, self._engine)
        self._lock = threading.Lock()
        self._adopt_legacy_file()

    # ---- legacy ------------------------------------------------------------------------------

    def _adopt_legacy_file(self) -> None:
        legacy = self._root / ".swarmkit" / "memory.json"
        if not legacy.exists():
            return
        try:
            entries = [MemoryEntry(**e) for e in json.loads(legacy.read_text())]
        except (json.JSONDecodeError, TypeError):
            logger.warning("%s is not a memory file; leaving it alone", legacy)
            return
        known = {e.id for e in self._all()}
        for entry in entries:
            if entry.id not in known:
                self._insert(entry)
        legacy.rename(legacy.with_suffix(".json.migrated"))
        logger.info("Moved %d memory entries from %s into the store", len(entries), legacy)

    # ---- rows --------------------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: Any) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            user=row["user"],
            session_id=row["session_id"],
            topic=row["topic"],
            context=row["context"],
            key_points=json.loads(row["key_points"] or "[]"),
            tags=json.loads(row["tags"] or "[]"),
            related_sessions=json.loads(row["related_sessions"] or "[]"),
            created_at=row["created_at"],
            source_agent=row["source_agent"],
        )

    def _all(self, *, user: str | None = None) -> list[MemoryEntry]:
        stmt = select(workspace_memory).order_by(workspace_memory.c.created_at)
        if user:
            stmt = stmt.where(
                or_(workspace_memory.c.user == user, workspace_memory.c.user.is_(None))
            )
        with self._engine.connect() as conn:
            return [self._row_to_entry(r) for r in conn.execute(stmt).mappings().all()]

    def _insert(self, entry: MemoryEntry) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(workspace_memory).values(
                    id=entry.id,
                    user=entry.user,
                    session_id=entry.session_id,
                    topic=entry.topic,
                    context=entry.context,
                    key_points=json.dumps(entry.key_points),
                    tags=json.dumps(entry.tags),
                    related_sessions=json.dumps(entry.related_sessions),
                    created_at=entry.created_at,
                    source_agent=entry.source_agent,
                )
            )

    # ---- API ---------------------------------------------------------------------------------

    def add(self, entry: MemoryEntry) -> None:
        with self._lock:
            if not entry.created_at:
                entry.created_at = datetime.now(UTC).isoformat()
            if not entry.id:
                entry.id = f"mem-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{self.count()}"
            self._insert(entry)
            logger.info(
                "Memory saved: id=%s topic=%r user=%s tags=%s",
                entry.id,
                entry.topic,
                entry.user,
                entry.tags,
            )

    def search(
        self,
        query: str,
        *,
        user: str | None = None,
        max_results: int = 5,
        min_score: float = 0.1,
    ) -> list[tuple[MemoryEntry, float]]:
        if not query.strip():
            return []
        candidates = self._all(user=user)
        if not candidates:
            return []
        return self._tfidf_search(query.lower(), candidates, max_results, min_score)

    def list_all(
        self,
        *,
        user: str | None = None,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        entries = self._all(user=user)
        return list(reversed(entries[-limit:]))

    def get(self, memory_id: str) -> MemoryEntry | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(select(workspace_memory).where(workspace_memory.c.id == memory_id))
                .mappings()
                .first()
            )
        return self._row_to_entry(row) if row is not None else None

    def delete(self, memory_id: str) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                delete(workspace_memory).where(workspace_memory.c.id == memory_id)
            )
        return result.rowcount > 0

    def delete_user(self, user: str) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(delete(workspace_memory).where(workspace_memory.c.user == user))
        return int(result.rowcount)

    def count(self, *, user: str | None = None) -> int:
        stmt = select(func.count()).select_from(workspace_memory)
        if user:
            stmt = stmt.where(workspace_memory.c.user == user)
        with self._engine.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def get_status(self) -> dict[str, Any]:
        entries = self._all()
        users = {e.user for e in entries if e.user}
        tags: Counter[str] = Counter()
        for e in entries:
            tags.update(e.tags)
        return {
            "total_entries": len(entries),
            "users": sorted(users),
            "top_tags": tags.most_common(10),
            "store": redacted_url(str(self._engine.url)),
        }

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _tfidf_search(
        self,
        query: str,
        candidates: list[MemoryEntry],
        max_results: int,
        min_score: float,
    ) -> list[tuple[MemoryEntry, float]]:
        """Score each memory by how much of the QUERY it covers: the idf-weighted share of the
        query's content words that appear in the memory, in [0, 1].

        The previous score summed ``tf * idf`` with ``tf = count / len(memory)``, so a memory's
        score fell with its length: a rich entry of 150 tokens scored 0.007 per matching word,
        and a query had to share ~15 words with it to clear the default ``min_score`` of 0.1.
        "Japan trip" against a memory titled "Japan trip planning" scored 0.068 — below the
        threshold — and the memory-reader injected nothing, ever, for any realistic memory. A
        coverage score asks the right question (is this memory about what the user is asking?)
        and does not care how much the memory knows.
        """
        query_tokens = [t for t in self._tokenize(query) if t not in _STOPWORDS]
        if not query_tokens:
            return []

        doc_token_sets = [set(self._tokenize(c.searchable_text)) for c in candidates]
        n_docs = len(candidates)

        df: Counter[str] = Counter()
        for tokens in doc_token_sets:
            df.update(tokens)

        idf = {
            token: math.log((n_docs + 1) / (df.get(token, 0) + 1)) + 1
            for token in set(query_tokens)
        }
        total = sum(idf[t] for t in set(query_tokens))

        scored: list[tuple[int, float]] = []
        for i, tokens in enumerate(doc_token_sets):
            if not tokens:
                continue
            covered = sum(idf[t] for t in set(query_tokens) if t in tokens)
            score = covered / total if total else 0.0
            if score >= min_score:
                scored.append((i, round(score, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [(candidates[i], s) for i, s in scored[:max_results]]


# Function words carry no topic; left in, "where was I planning to go" is mostly noise that no
# memory can match and the coverage score of every memory is capped by it.
_STOPWORDS = frozenset(
    re.findall(
        r"[a-z]+",
        "a an and are as at be but by can could did do does for from had has have he her his how i "
        "if in is it its me my of on or our she so that the their them then there these they this "
        "to was we were what when where which who why will with would you your remind about",
    )
)


__all__ = ["MemoryEntry", "MemoryStore"]
