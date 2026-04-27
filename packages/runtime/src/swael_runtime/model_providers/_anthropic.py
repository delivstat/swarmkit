"""AnthropicModelProvider — wraps the ``anthropic`` SDK.

Only this file imports ``anthropic``. The rest of the runtime goes
through the ModelProvider interface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic

from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)

_CLAUDE_PREFIXES = ("claude-",)


class AnthropicModelProvider:
    """ModelProvider for Anthropic's Claude models."""

    provider_id: str = "anthropic"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, **kwargs)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        messages = _to_anthropic_messages(request)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
        }
        if request.system:
            kwargs["system"] = request.system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema or {"type": "object", "properties": {}},
                }
                for t in request.tools
            ]
        if request.extra:
            kwargs.update(request.extra)

        raw = await self._client.messages.create(**kwargs)
        return _from_anthropic_response(raw)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        messages = _to_anthropic_messages(request)
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
        }
        if request.system:
            kwargs["system"] = request.system
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield ContentBlock(type="text", text=text)

    def supports(self, model: str) -> bool:
        return any(model.startswith(p) for p in _CLAUDE_PREFIXES)

    def tokenize(self, text: str, model: str) -> int | None:
        return None


def _to_anthropic_messages(
    request: CompletionRequest,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for msg in request.messages:
        if msg.role == "system":
            continue
        if isinstance(msg.content, str):
            messages.append({"role": msg.role, "content": msg.content})
        else:
            blocks: list[dict[str, Any]] = []
            for block in msg.content:
                if block.type == "text":
                    blocks.append({"type": "text", "text": block.text or ""})
                elif block.type == "tool_use":
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.tool_use_id or "",
                            "name": block.tool_name or "",
                            "input": block.tool_input or {},
                        }
                    )
                elif block.type == "tool_result":
                    blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.tool_use_id or "",
                            "content": str(block.tool_result) if block.tool_result else "",
                        }
                    )
            messages.append({"role": msg.role, "content": blocks})
    return messages


def _from_anthropic_response(raw: Any) -> CompletionResponse:
    blocks: list[ContentBlock] = []
    for block in raw.content:
        if block.type == "text":
            blocks.append(ContentBlock(type="text", text=block.text))
        elif block.type == "tool_use":
            blocks.append(
                ContentBlock(
                    type="tool_use",
                    tool_use_id=block.id,
                    tool_name=block.name,
                    tool_input=block.input,
                )
            )

    stop_map = {
        "end_turn": "end_turn",
        "max_tokens": "max_tokens",
        "tool_use": "tool_use",
    }
    stop_reason: Any = stop_map.get(raw.stop_reason, "end_turn")

    return CompletionResponse(
        content=tuple(blocks),
        stop_reason=stop_reason,
        usage=Usage(
            input_tokens=raw.usage.input_tokens,
            output_tokens=raw.usage.output_tokens,
            cache_read_tokens=getattr(raw.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(raw.usage, "cache_creation_input_tokens", 0) or 0,
        ),
        raw=raw,
    )
