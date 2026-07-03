"""Canonical types for the ModelProvider abstraction.

Internal message format follows Anthropic's messages shape (roles,
content blocks, tool_use / tool_result) because it is the most expressive
of the major providers. Provider adapters translate to/from it.

See ``design/details/model-provider-abstraction.md``.
"""

from __future__ import annotations

import os
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
    response_format: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None
    # Provider-native runtime options carried from the artifact's ``model.options``
    # block (e.g. Ollama ``num_ctx`` / ``repeat_penalty``, OpenAI ``top_p`` /
    # ``frequency_penalty``). Each provider folds these into its native call —
    # for Ollama into the ``options`` object, for the others as top-level call
    # parameters. Applied after the first-class fields, so an option with the
    # same name (e.g. ``temperature``) overrides them.
    options: dict[str, Any] | None = None


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


_MODEL_TIMEOUT = int(os.environ.get("SWARMKIT_MODEL_TIMEOUT", "300"))
_MODEL_RETRIES = int(os.environ.get("SWARMKIT_MODEL_RETRIES", "3"))


async def with_retry(
    coro_fn: Any,
    *,
    timeout: int = 0,
    max_retries: int = 0,
    label: str = "model call",
) -> Any:
    """Wrap an async call with timeout + exponential backoff + jitter.

    Retries on timeout, connection, and transient HTTP errors. Raises
    on non-retryable errors (auth, validation, etc.).

    Configurable via env:
    - ``SWARMKIT_MODEL_TIMEOUT`` — per-call timeout in seconds (default 300)
    - ``SWARMKIT_MODEL_RETRIES`` — max retries (default 3)
    """
    import asyncio  # noqa: PLC0415
    import random  # noqa: PLC0415
    import sys  # noqa: PLC0415

    _timeout = timeout or _MODEL_TIMEOUT
    _retries = max_retries or _MODEL_RETRIES
    _verbose = os.environ.get("SWARMKIT_VERBOSE", "")

    for attempt in range(_retries + 1):
        try:
            return await asyncio.wait_for(coro_fn(), timeout=_timeout)
        except TimeoutError:
            if attempt == _retries:
                raise
            wait = (2**attempt) + random.uniform(0, 1)
            if _verbose:
                print(
                    f"  [timeout: {label} attempt {attempt + 1}/{_retries + 1}, "
                    f"retrying in {wait:.1f}s]",
                    file=sys.stderr,
                )
            await asyncio.sleep(wait)
        except Exception as exc:
            exc_name = type(exc).__name__
            is_retryable = any(
                s in exc_name.lower()
                for s in ("timeout", "connection", "server", "rate", "502", "503", "529")
            ) or any(
                s in str(exc).lower()
                for s in ("timeout", "connection reset", "server error", "rate limit", "overloaded")
            )
            if not is_retryable or attempt == _retries:
                raise
            wait = (2**attempt) + random.uniform(0, 1)
            if _verbose:
                print(
                    f"  [error: {label} attempt {attempt + 1}/{_retries + 1}: "
                    f"{exc_name}, retrying in {wait:.1f}s]",
                    file=sys.stderr,
                )
            await asyncio.sleep(wait)

    raise RuntimeError(f"{label} failed after {_retries + 1} attempts")


_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
}


# Per-model ``options`` are vendor-shaped. These keys are Ollama-specific runtime knobs
# (context window, GPU placement, keep-alive, sampler extras) that non-Ollama SDKs reject
# as unknown kwargs — fatal when a topology authored for Ollama is repointed at an
# OpenAI-compatible provider, Anthropic, or Google. Every non-Ollama adapter drops them
# via ``apply_options``; Ollama folds them natively. Genuine cross-vendor params (top_p,
# frequency_penalty, presence_penalty, seed, stop, …) pass through untouched.
NON_NATIVE_OPTIONS = frozenset(
    {
        "num_ctx",
        "num_gpu",
        "num_predict",
        "num_thread",
        "num_keep",
        "num_batch",
        "main_gpu",
        "low_vram",
        "numa",
        "f16_kv",
        "use_mmap",
        "use_mlock",
        "vocab_only",
        "keep_alive",
        "think",
        "top_k",
        "min_p",
        "tfs_z",
        "typical_p",
        "repeat_penalty",
        "repeat_last_n",
        "penalize_newline",
        "mirostat",
        "mirostat_eta",
        "mirostat_tau",
    }
)


def apply_options(
    kwargs: dict[str, Any],
    options: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
    *,
    drop: frozenset[str] = NON_NATIVE_OPTIONS,
) -> dict[str, Any]:
    """Fold per-model ``options`` (minus vendor-incompatible ``drop`` keys) and then the
    runtime ``extra`` (never filtered — it is authoritative passthrough) into ``kwargs``.
    Mutates and returns ``kwargs``. Used by every non-Ollama adapter so the drop-set lives
    in exactly one place."""
    if options:
        kwargs.update({k: v for k, v in options.items() if k not in drop})
    if extra:
        kwargs.update(extra)
    return kwargs


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
