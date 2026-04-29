"""Model provider abstraction — the single seam through which the runtime
reaches any LLM (Anthropic, OpenAI, Google, Ollama, or custom).

See ``design/details/model-provider-abstraction.md`` for the full spec.

Non-negotiable invariant: **only this package may import LLM SDKs**
(``anthropic``, ``google-genai``, ``openai``, Ollama's HTTP client).
Every other module in ``swarmkit_runtime`` receives a provider instance
or a ``CompletionRequest`` and never touches a vendor SDK directly.
Same rule as ``governance/`` for AGT. See root CLAUDE.md invariant #4.
"""

from ._mock import MockModelProvider
from ._registry import ProviderRegistry
from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Message,
    ToolSpec,
    Usage,
)

_LAZY_PROVIDERS: dict[str, tuple[str, str]] = {
    "AnthropicModelProvider": ("._anthropic", "AnthropicModelProvider"),
    "GoogleModelProvider": ("._google", "GoogleModelProvider"),
    "OllamaModelProvider": ("._ollama", "OllamaModelProvider"),
    "OpenAIModelProvider": ("._openai", "OpenAIModelProvider"),
    "GroqModelProvider": ("._openai_compat", "GroqModelProvider"),
    "OpenRouterModelProvider": ("._openai_compat", "OpenRouterModelProvider"),
    "TogetherModelProvider": ("._openai_compat", "TogetherModelProvider"),
}


def __getattr__(name: str) -> type:
    """Lazy-load provider classes to avoid import errors when SDKs are missing."""
    if name in _LAZY_PROVIDERS:
        module_path, class_name = _LAZY_PROVIDERS[name]
        import importlib  # noqa: PLC0415

        mod = importlib.import_module(module_path, __name__)
        return getattr(mod, class_name)  # type: ignore[no-any-return]
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


__all__ = [
    "AnthropicModelProvider",
    "CompletionRequest",
    "CompletionResponse",
    "ContentBlock",
    "GoogleModelProvider",
    "GroqModelProvider",
    "Message",
    "MockModelProvider",
    "OllamaModelProvider",
    "OpenAIModelProvider",
    "OpenRouterModelProvider",
    "ProviderRegistry",
    "TogetherModelProvider",
    "ToolSpec",
    "Usage",
]
