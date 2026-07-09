"""Per-model price table — derive cost for providers that report only tokens.

OpenRouter returns per-call cost natively (PR 1); the first-party providers (Anthropic, OpenAI,
Google) return only token counts. This table maps a model to its published price so
``_record_run_usage`` can fill ``cost_usd`` from tokens when the provider didn't supply it. Ollama
is local — no entry means $0, which is correct.

Prices are **USD per 1,000,000 tokens** ``(input, output)`` — approximate public list prices; they
drift, so treat this as a best-effort estimate and update it when a provider changes pricing. A
model with no matching entry contributes ``$0`` (unknown, not guessed).
"""

from __future__ import annotations

# USD per 1M tokens: (input, output). Keyed by a normalized model prefix (provider prefix stripped,
# lowercased). Longest matching key wins, so specific variants (gpt-4o-mini) beat generic (gpt-4o).
_PRICES_PER_M: dict[str, tuple[float, float]] = {
    # Anthropic Claude
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.80, 4.0),
    "claude-3-haiku": (0.25, 1.25),
    "claude-3-opus": (15.0, 75.0),
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.0, 8.0),
    "o3-mini": (1.10, 4.40),
    "o1-mini": (1.10, 4.40),
    "o1": (15.0, 60.0),
    # Google Gemini
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.0),
    "gemini-1.5-flash": (0.075, 0.30),
}


def _normalize(model: str) -> str:
    """Strip an aggregator's provider prefix (``anthropic/claude-sonnet-4`` → ``claude-sonnet-4``)
    and lowercase, so a table keyed by bare model names matches both native and routed ids."""
    bare = model.rsplit("/", 1)[-1]
    return bare.lower()


def price_per_million(model: str) -> tuple[float, float] | None:
    """Return ``(input, output)`` USD-per-1M for *model*, or ``None`` if unpriced. Matches by the
    longest table key that the normalized model id starts with (so dated/variant suffixes like
    ``-20250514`` or ``-2024-08-06`` still resolve)."""
    norm = _normalize(model)
    for key in sorted(_PRICES_PER_M, key=len, reverse=True):
        if norm.startswith(key):
            return _PRICES_PER_M[key]
    return None


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate a call's cost in USD from its tokens and the price table. Returns ``0.0`` for an
    unpriced model (unknown → not guessed) — used only when the provider reported no cost itself."""
    price = price_per_million(model)
    if price is None:
        return 0.0
    input_per_m, output_per_m = price
    return input_tokens / 1_000_000 * input_per_m + output_tokens / 1_000_000 * output_per_m


__all__ = ["estimate_cost", "price_per_million"]
