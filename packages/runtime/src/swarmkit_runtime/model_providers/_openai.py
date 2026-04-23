"""OpenAIModelProvider — wraps the ``openai`` SDK.

Also handles Azure OpenAI via ``extra.base_url`` + Azure auth. Only
this file imports ``openai``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import openai

from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)

_OPENAI_PREFIXES = ("gpt-", "o1-", "o3-", "o4-")


class OpenAIModelProvider:
    """ModelProvider for OpenAI's GPT / o-series models."""

    provider_id: str = "openai"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, **kwargs)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        kwargs = _to_openai_kwargs(request)
        raw = await self._client.chat.completions.create(**kwargs)
        return _from_openai_response(raw)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        kwargs = _to_openai_kwargs(request)
        kwargs["stream"] = True
        async for chunk in await self._client.chat.completions.create(**kwargs):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield ContentBlock(type="text", text=delta.content)

    def supports(self, model: str) -> bool:
        return any(model.startswith(p) for p in _OPENAI_PREFIXES)

    def tokenize(self, text: str, model: str) -> int | None:
        return None


def _to_openai_kwargs(request: CompletionRequest) -> dict[str, Any]:
    messages = _build_openai_messages(request)
    kwargs: dict[str, Any] = {"model": request.model, "messages": messages}
    if request.max_tokens is not None:
        kwargs["max_tokens"] = request.max_tokens
    if request.temperature is not None:
        kwargs["temperature"] = request.temperature
    if request.tools:
        kwargs["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema or {"type": "object", "properties": {}},
                },
            }
            for t in request.tools
        ]
    if request.extra:
        kwargs.update(request.extra)
    return kwargs


def _build_openai_messages(request: CompletionRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    for msg in request.messages:
        if isinstance(msg.content, str):
            messages.append({"role": msg.role, "content": msg.content})
        else:
            parts: list[dict[str, Any]] = []
            for block in msg.content:
                if block.type == "text":
                    parts.append({"type": "text", "text": block.text or ""})
                elif block.type == "tool_use":
                    messages.append(
                        {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": block.tool_use_id or "",
                                    "type": "function",
                                    "function": {
                                        "name": block.tool_name or "",
                                        "arguments": str(block.tool_input or {}),
                                    },
                                }
                            ],
                        }
                    )
                    continue
                elif block.type == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.tool_use_id or "",
                            "content": str(block.tool_result) if block.tool_result else "",
                        }
                    )
                    continue
            if parts:
                messages.append({"role": msg.role, "content": parts})
    return messages


def _from_openai_response(raw: Any) -> CompletionResponse:
    choice = raw.choices[0] if raw.choices else None
    blocks: list[ContentBlock] = []

    if choice and choice.message.content:
        blocks.append(ContentBlock(type="text", text=choice.message.content))

    if choice and choice.message.tool_calls:
        for tc in choice.message.tool_calls:
            blocks.append(
                ContentBlock(
                    type="tool_use",
                    tool_use_id=tc.id,
                    tool_name=tc.function.name,
                    tool_input=tc.function.arguments,
                )
            )

    stop_reason: Any = "end_turn"
    if choice:
        fr = choice.finish_reason
        if fr == "length":
            stop_reason = "max_tokens"
        elif fr == "tool_calls":
            stop_reason = "tool_use"

    usage = Usage(
        input_tokens=getattr(raw.usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(raw.usage, "completion_tokens", 0) or 0,
    )

    return CompletionResponse(
        content=tuple(blocks),
        stop_reason=stop_reason,
        usage=usage,
        raw=raw,
    )
