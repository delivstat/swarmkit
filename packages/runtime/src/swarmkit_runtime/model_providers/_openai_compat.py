"""OpenAI-compatible model providers for aggregators (OpenRouter, Groq, Together, etc).

These are thin wrappers around the openai SDK with custom base URLs.
They accept any model name since aggregator model catalogues are
unbounded — the aggregator validates model names, not the provider.
"""

from __future__ import annotations

import os
from typing import Any

from ._openai import OpenAIModelProvider


class OpenRouterModelProvider(OpenAIModelProvider):
    """OpenRouter (openrouter.ai) — free + paid models via OpenAI-compatible API."""

    provider_id: str = "openrouter"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        key = api_key or os.environ.get("OPENROUTER_API_KEY")
        super().__init__(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            **kwargs,
        )

    def supports(self, model: str) -> bool:
        return True


class GroqModelProvider(OpenAIModelProvider):
    """Groq (groq.com) — fast inference, generous free tier."""

    provider_id: str = "groq"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        key = api_key or os.environ.get("GROQ_API_KEY")
        super().__init__(
            api_key=key,
            base_url="https://api.groq.com/openai/v1",
            **kwargs,
        )

    def supports(self, model: str) -> bool:
        return True


class TogetherModelProvider(OpenAIModelProvider):
    """Together AI (together.ai) — open models, free tier."""

    provider_id: str = "together"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        key = api_key or os.environ.get("TOGETHER_API_KEY")
        super().__init__(
            api_key=key,
            base_url="https://api.together.xyz/v1",
            **kwargs,
        )

    def supports(self, model: str) -> bool:
        return True
