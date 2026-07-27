"""Demo: relevance-ranked retrieval for governed memory (design/details/governed-memory.md).

A query returns the most relevant memories — via a local TF-IDF score (no keys), or cosine
similarity when an embedder is wired — not merely any that contain a substring. Deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import create_engine
from swarmkit_runtime.governed_memory import GovernedMemoryStore, MemoryCandidate

_FACTS = [
    ("user:alice", "preferred_language", "she writes Python and Rust daily"),
    ("user:alice", "editor", "she uses the neovim editor with tmux"),
    ("project:oms", "database", "the order service runs on Postgres"),
    ("project:oms", "queue", "events flow through Kafka topics"),
]


def _seed(store: GovernedMemoryStore) -> None:
    for subject, attribute, value in _FACTS:
        store.write(MemoryCandidate(subject=subject, attribute=attribute, value=value))


def _show(store: GovernedMemoryStore, label: str) -> None:
    print(f"\n{label}")
    for query in ("python", "neovim editor", "postgres kafka"):
        hits = [f"{m.subject}·{m.attribute}" for m in store.search(query)]
        print(f"  search({query!r:18}) → {hits}")


def main() -> None:
    lexical = GovernedMemoryStore(create_engine("sqlite:///:memory:"))
    _seed(lexical)
    _show(lexical, "Lexical (default, TF-IDF, no keys):")

    # A toy embedder — real deployments wire a local model or an MCP embedder here.
    def embed(text: str) -> Sequence[float]:
        t = text.lower()
        vocab = ("python", "rust", "neovim", "editor", "postgres", "kafka")
        return [float(t.count(w)) for w in vocab]

    vector = GovernedMemoryStore(create_engine("sqlite:///:memory:"), embedder=embed)
    _seed(vector)
    _show(vector, "Embedding (opt-in seam, cosine similarity):")

    print("\n✓ queries surface the most relevant facts; the embedder seam has no vendor lock-in.")


if __name__ == "__main__":
    main()
