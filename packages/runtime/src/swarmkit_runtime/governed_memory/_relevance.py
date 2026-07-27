"""Relevance ranking for governed-memory search (design/details/governed-memory.md).

Two ranking modes, no vendor lock-in:

* **Lexical** (default, dependency-free, no keys) — a TF-IDF score over the memory corpus, the same
  local approach the conversation-recall store uses. Better than substring: it ranks by term
  importance, so a query surfaces the most relevant facts rather than any that merely contain the
  substring.
* **Embedding** (opt-in) — when an :data:`Embedder` is wired into the store, rank by cosine
  similarity between the query and each memory. The seam is a plain callable, so any provider (a
  local model, an MCP embedder) plugs in without the store depending on it.

Both feed the store's ranking, which still folds in confidence/decay as a secondary signal.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence

#: Embed a text into a vector. Injected into the store to enable cosine-similarity search; the store
#: never imports an embedding provider itself (mirrors the ModelProvider no-lock-in rule).
Embedder = Callable[[str], Sequence[float]]


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def lexical_scores(query: str, docs: Sequence[str]) -> list[float]:
    """TF-IDF relevance of ``query`` against each document. 0.0 for a doc sharing no query term."""
    query_tokens = tokenize(query)
    if not query_tokens or not docs:
        return [0.0] * len(docs)

    doc_tokens = [tokenize(d) for d in docs]
    n_docs = len(docs)
    df: dict[str, int] = {}
    for tokens in doc_tokens:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    idf = {t: math.log((n_docs + 1) / (df.get(t, 0) + 1)) + 1 for t in set(query_tokens)}

    scores: list[float] = []
    for tokens in doc_tokens:
        if not tokens:
            scores.append(0.0)
            continue
        tf: dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        score = sum((tf[qt] / len(tokens)) * idf.get(qt, 1.0) for qt in query_tokens if qt in tf)
        scores.append(score)
    return scores


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors; 0.0 if either is empty or zero-magnitude."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def embedding_scores(query: str, docs: Sequence[str], embedder: Embedder) -> list[float]:
    """Cosine similarity of ``query`` against each document, via ``embedder``."""
    if not docs:
        return []
    q = embedder(query)
    return [cosine(q, embedder(d)) for d in docs]
