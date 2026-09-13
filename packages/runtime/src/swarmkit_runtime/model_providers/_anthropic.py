"""AnthropicModelProvider — wraps the ``anthropic`` SDK.

Only this file imports ``anthropic``. The rest of the runtime goes
through the ModelProvider interface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import anthropic

from ._family import FamilyBase
from ._types import (
    NON_NATIVE_OPTIONS,
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
    apply_options,
)

_CLAUDE_PATTERN = r"^claude-"
# Anthropic's messages.create accepts top_k (unlike the OpenAI chat API), so keep it.
_DROP = NON_NATIVE_OPTIONS - frozenset({"top_k"})


class AnthropicModelProvider(FamilyBase):
    """ModelProvider for Anthropic's Claude models — the ``anthropic`` family."""

    provider_id: str = "anthropic"

    #: Anthropic has no schema-constrained decoding — it does not read ``response_format``
    #: at all, so the schema must stay in the prompt or nothing carries it.
    enforces_response_schema: bool = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        provider_id: str | None = None,
        base_url: str | None = None,
        auth: Any = None,
        headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, Any] | None = None,
        lift_to_root: tuple[str, ...] | None = None,
        model_pattern: str | None = None,
        accept_any_model: bool = False,
        capabilities: Mapping[str, bool] | None = None,
        **kwargs: Any,
    ) -> None:
        self._configure(
            provider_id=provider_id,
            model_pattern=model_pattern,
            accept_any_model=accept_any_model,
            capabilities=capabilities,
            default_pattern=_CLAUDE_PATTERN,
            default_accept_any=False,
            headers=headers,
        )
        # ``auth``, ``extra_body`` and ``lift_to_root`` are accepted for a uniform build call and
        # refused upstream (`_declarative.FAMILY_FIELDS`): this family owns its wire format.
        if base_url is not None:
            kwargs.setdefault("base_url", base_url)
        if self.extra_headers:
            kwargs.setdefault("default_headers", dict(self.extra_headers))
        self._client = anthropic.AsyncAnthropic(api_key=api_key, **kwargs)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        from ._types import with_retry  # noqa: PLC0415

        self._check(request)
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
        # Drop Ollama-only options the Anthropic SDK would reject (num_ctx, keep_alive, …);
        # to force a filtered key through, use ``extra`` (never filtered).
        apply_options(kwargs, request.options, request.extra, drop=_DROP)

        raw = await with_retry(
            lambda: self._client.messages.create(**kwargs),
            label=f"anthropic:{request.model}",
        )
        return _from_anthropic_response(raw)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        self._check(request)
        self._check_stream()
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
        apply_options(kwargs, request.options, request.extra, drop=_DROP)

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield ContentBlock(type="text", text=text)

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
                elif block.type == "image" and block.image_data:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": block.image_media_type or "image/png",
                                "data": block.image_data,
                            },
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
