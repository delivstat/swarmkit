# ruff: noqa: I001
"""Embedding backend for intent drift detection.

Default: sentence-transformers (local, no API keys needed).
Falls back to TF-IDF if sentence-transformers is not installed.

The embedding function is lazy-loaded on first use to avoid import
overhead when drift detection is not enabled.
"""

from __future__ import annotations

import math
from typing import Any

_model: Any = None
_use_tfidf = False


def embed(text: str) -> list[float]:
    """Embed a text string into a vector. Lazy-loads the backend."""
    global _model, _use_tfidf  # noqa: PLW0603

    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]  # noqa: PLC0415

            _model = SentenceTransformer("all-MiniLM-L6-v2", backend="onnx")
            _use_tfidf = False
        except ImportError:
            _model = _TFIDFEmbedder()
            _use_tfidf = True

    if _use_tfidf:
        tfidf_result: list[float] = _model.embed(text)
        return tfidf_result

    result = _model.encode([text], normalize_embeddings=True)
    vec: list[float] = list(result[0].tolist())
    return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        min_len = min(len(a), len(b))
        a = a[:min_len]
        b = b[:min_len]

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class _TFIDFEmbedder:
    """Simple TF-IDF fallback when sentence-transformers is not available.

    Uses a fixed-dimension hash-based embedding so vectors are always
    comparable regardless of vocabulary differences.
    """

    _DIM = 128

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._DIM
        tokens = text.lower().split()
        for token in tokens:
            idx = hash(token) % self._DIM
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]
