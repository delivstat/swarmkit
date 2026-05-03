"""Canonical types for the ModelProvider abstraction.

Internal message format follows Anthropic's messages shape (roles,
content blocks, tool_use / tool_result) because it is the most expressive
of the major providers. Provider adapters translate to/from it.

See ``design/details/model-provider-abstraction.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ContentBlock:
    """A single content block within a message or response."""

    type: Literal["text", "tool_use", "tool_result", "image"]
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: Any = None


@dataclass(frozen=True)
class Message:
    """A conversation message in the canonical SwarmKit format."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | Sequence[ContentBlock]


@dataclass(frozen=True)
class ToolSpec:
    """Canonical tool definition passed to the model."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    """Token usage from a completion."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class CompletionRequest:
    """Everything needed to make a model call."""

    model: str
    messages: Sequence[Message]
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: Sequence[ToolSpec] | None = None
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class CompletionResponse:
    """The model's response in canonical format."""

    content: Sequence[ContentBlock]
    stop_reason: Literal["end_turn", "max_tokens", "tool_use", "error"]
    usage: Usage
    raw: Any = None

    @property
    def text(self) -> str:
        """Extract joined text from all text blocks."""
        parts = [b.text for b in self.content if b.type == "text" and b.text]
        return "\n".join(parts)
