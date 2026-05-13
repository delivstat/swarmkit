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
    """A single content block within a message or response.

    For ``type="image"``, set ``image_data`` (base64-encoded bytes) and
    ``image_media_type`` (e.g. ``"image/png"``). Use :func:`image_block`
    to construct image blocks from file paths.
    """

    type: Literal["text", "tool_use", "tool_result", "image"]
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: Any = None
    image_data: str | None = None
    image_media_type: str | None = None


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


_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
}


def image_block(path: str) -> ContentBlock:
    """Create an image ContentBlock from a file path.

    Reads the file, base64-encodes it, and infers the media type from
    the extension. Raises ``FileNotFoundError`` if the file doesn't exist
    and ``ValueError`` for unsupported image formats.
    """
    import base64  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Image file not found: {path}")
    ext = p.suffix.lower()
    media_type = _MEDIA_TYPES.get(ext)
    if media_type is None:
        raise ValueError(
            f"Unsupported image format '{ext}'. Supported: {', '.join(sorted(_MEDIA_TYPES.keys()))}"
        )
    data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return ContentBlock(type="image", image_data=data, image_media_type=media_type)
