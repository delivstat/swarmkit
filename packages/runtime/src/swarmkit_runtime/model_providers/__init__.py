"""Model provider abstraction — the single seam through which the runtime
reaches any LLM (Anthropic, OpenAI, Google, Ollama, or custom).

See ``design/details/model-provider-abstraction.md`` for the full spec.

Non-negotiable invariant: **only this package may import LLM SDKs**
(``anthropic``, ``google-genai``, ``openai``, Ollama's HTTP client).
Every other module in ``swarmkit_runtime`` receives a provider instance
or a ``CompletionRequest`` and never touches a vendor SDK directly.
Same rule as ``governance/`` for AGT. See root CLAUDE.md invariant #4.
"""

from ._anthropic import AnthropicModelProvider
from ._google import GoogleModelProvider
from ._mock import MockModelProvider
from ._ollama import OllamaModelProvider
from ._openai import OpenAIModelProvider
from ._registry import ProviderRegistry
from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Message,
    ToolSpec,
    Usage,
)

__all__ = [
    "AnthropicModelProvider",
    "CompletionRequest",
    "CompletionResponse",
    "ContentBlock",
    "GoogleModelProvider",
    "Message",
    "MockModelProvider",
    "OllamaModelProvider",
    "OpenAIModelProvider",
    "ProviderRegistry",
    "ToolSpec",
    "Usage",
]
