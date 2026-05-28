"""Workspace memory store — persistent knowledge that grows with use.

Stores structured insights extracted from conversations. Each entry
captures a topic, context, key points, and links to related sessions.
Supports semantic search via TF-IDF (local, no API keys).

See design/details/workspace-memory.md.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    """File-backed memory store with TF-IDF search.

    Stores memories as a JSON array in ``{workspace}/.swarmkit/memory.json``.
    Thread-safe for concurrent reads/writes.

    Parameters
    ----------
    workspace_path:
        Root of the workspace directory. Memory file lives at
        ``{workspace_path}/.swarmkit/memory.json``.
    """

    def __init__(self, workspace_path: Path) -> None:
        self._dir = workspace_path / ".swarmkit"
        self._path = self._dir / "memory.json"
        self._lock = threading.Lock()
        self._entries: list[MemoryEntry] = []
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._entries = [MemoryEntry(**e) for e in data]
                logger.info("Loaded %d memory entries from %s", len(self._entries), self._path)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to load memory from %s; starting fresh", self._path)
                self._entries = []
        else:
            self._entries = []

    def _save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps([asdict(e) for e in self._entries], indent=2, default=str))

    def add(self, entry: MemoryEntry) -> None:
        with self._lock:
            if not entry.created_at:
                entry.created_at = datetime.now(UTC).isoformat()
            if not entry.id:
                entry.id = f"mem-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{len(self._entries)}"
            self._entries.append(entry)
            self._save()
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
        with self._lock:
            candidates = self._entries
            if user:
                candidates = [e for e in candidates if e.user == user or e.user is None]

            if not candidates or not query.strip():
                return []

            return self._tfidf_search(query.lower(), candidates, max_results, min_score)

    def list_all(
        self,
        *,
        user: str | None = None,
        limit: int = 50,
    ) -> list[MemoryEntry]:
        with self._lock:
            entries = self._entries
            if user:
                entries = [e for e in entries if e.user == user or e.user is None]
            return list(reversed(entries[-limit:]))

    def get(self, memory_id: str) -> MemoryEntry | None:
        with self._lock:
            return next((e for e in self._entries if e.id == memory_id), None)

    def delete(self, memory_id: str) -> bool:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.id != memory_id]
            if len(self._entries) < before:
                self._save()
                return True
            return False

    def delete_user(self, user: str) -> int:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.user != user]
            removed = before - len(self._entries)
            if removed > 0:
                self._save()
            return removed

    def count(self, *, user: str | None = None) -> int:
        with self._lock:
            if user:
                return sum(1 for e in self._entries if e.user == user)
            return len(self._entries)

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            users = {e.user for e in self._entries if e.user}
            tags: Counter[str] = Counter()
            for e in self._entries:
                tags.update(e.tags)
            return {
                "total_entries": len(self._entries),
                "users": sorted(users),
                "top_tags": tags.most_common(10),
                "path": str(self._path),
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
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        doc_texts = [c.searchable_text for c in candidates]
        doc_token_lists = [self._tokenize(t) for t in doc_texts]
        n_docs = len(candidates)

        df: Counter[str] = Counter()
        for tokens in doc_token_lists:
            df.update(set(tokens))

        idf: dict[str, float] = {}
        for token in set(query_tokens):
            doc_freq = df.get(token, 0)
            idf[token] = math.log((n_docs + 1) / (doc_freq + 1)) + 1

        scored: list[tuple[int, float]] = []
        for i, tokens in enumerate(doc_token_lists):
            if not tokens:
                continue
            tf: Counter[str] = Counter(tokens)
            score = 0.0
            for qt in query_tokens:
                if qt in tf:
                    term_freq = tf[qt] / len(tokens)
                    score += term_freq * idf.get(qt, 1.0)
            if score >= min_score:
                scored.append((i, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [(candidates[i], s) for i, s in scored[:max_results]]


__all__ = ["MemoryEntry", "MemoryStore"]
