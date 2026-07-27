"""Relevance-ranked retrieval for governed memory (design/details/governed-memory.md).

A query returns the most *relevant* memories, not any that contain a substring — via a local TF-IDF
score by default, or cosine similarity when an embedder is wired. Empty queries keep the
confidence/recency ranking.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import create_engine
from swarmkit_runtime.governed_memory import GovernedMemoryStore, MemoryCandidate
from swarmkit_runtime.governed_memory._relevance import cosine, lexical_scores


def _store(**kw: object) -> GovernedMemoryStore:
    return GovernedMemoryStore(create_engine("sqlite:///:memory:"), **kw)  # type: ignore[arg-type]


def _seed(store: GovernedMemoryStore) -> None:
    for subject, attribute, value in [
        ("user:alice", "preferred_language", "she writes Python and Rust daily"),
        ("user:alice", "editor", "she uses the neovim editor"),
        ("project:oms", "database", "the order service runs on Postgres"),
    ]:
        store.write(MemoryCandidate(subject=subject, attribute=attribute, value=value))


# ── pure scorers ─────────────────────────────────────────────────────────────────────────────────
def test_lexical_scores_rank_term_overlap() -> None:
    docs = ["python and rust", "the neovim editor", "postgres database"]
    scores = lexical_scores("python", docs)
    assert scores[0] > 0 and scores[1] == 0.0 and scores[2] == 0.0


def test_cosine_basics() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0


# ── lexical search (default, no keys) ────────────────────────────────────────────────────────────
def test_search_ranks_by_relevance_not_substring() -> None:
    store = _store()
    _seed(store)
    # "python" only matches the language memory — the substring "editor" query would not have found
    # it, but relevance ranking surfaces exactly the relevant fact.
    hits = store.search("python")
    assert [m.attribute for m in hits] == ["preferred_language"]

    # a multi-word query ranks the most relevant first
    ranked = store.search("neovim editor")
    assert ranked[0].attribute == "editor"

    # an irrelevant query returns nothing (only relevant memories come back)
    assert store.search("kubernetes") == []


def test_empty_query_still_confidence_ranked() -> None:
    store = _store()
    store.write(MemoryCandidate(subject="s", attribute="a", value="v1", confidence=0.9))
    store.write(MemoryCandidate(subject="s", attribute="b", value="v2", confidence=0.4))
    assert [m.attribute for m in store.search()] == ["a", "b"]  # highest confidence first


# ── embedding search (opt-in seam) ───────────────────────────────────────────────────────────────
def test_wired_embedder_ranks_by_cosine() -> None:
    # A toy embedder: 3 dims counting occurrences of "python", "editor", "postgres". Semantically
    # ranks a memory by which concept it is about, independent of exact query tokens.
    def embed(text: str) -> Sequence[float]:
        t = text.lower()
        return [float(t.count("python")), float(t.count("editor")), float(t.count("postgres"))]

    store = _store(embedder=embed)
    _seed(store)
    top = store.search("python")
    assert top[0].attribute == "preferred_language"
    # the editor memory ranks top for an editor query, via the embedder (not a token match)
    assert store.search("editor")[0].attribute == "editor"
