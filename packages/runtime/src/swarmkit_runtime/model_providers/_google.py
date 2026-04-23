"""GoogleModelProvider — wraps the ``google-genai`` SDK.

Only this file imports ``google.genai``. The rest of the runtime goes
through the ModelProvider interface.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from google import genai
from google.genai import types as gtypes

from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)

_GEMINI_PREFIXES = ("gemini-",)


class GoogleModelProvider:
    """ModelProvider for Google's Gemini models via google-genai."""

    provider_id: str = "google"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        self._client = genai.Client(api_key=api_key, **kwargs)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        contents = _to_google_contents(request)
        config = _to_google_config(request)

        raw = await self._client.aio.models.generate_content(
            model=request.model,
            contents=contents,
            config=config,
        )
        return _from_google_response(raw)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        contents = _to_google_contents(request)
        config = _to_google_config(request)

        async for chunk in await self._client.aio.models.generate_content_stream(
            model=request.model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                yield ContentBlock(type="text", text=chunk.text)

    def supports(self, model: str) -> bool:
        return any(model.startswith(p) for p in _GEMINI_PREFIXES)

    def tokenize(self, text: str, model: str) -> int | None:
        return None


def _to_google_contents(
    request: CompletionRequest,
) -> list[gtypes.Content]:
    contents: list[gtypes.Content] = []

    if request.system:
        contents.append(
            gtypes.Content(
                parts=[gtypes.Part(text=request.system)],
                role="user",
            )
        )
        contents.append(
            gtypes.Content(
                parts=[gtypes.Part(text="Understood.")],
                role="model",
            )
        )

    for msg in request.messages:
        if msg.role == "system":
            continue
        role = "model" if msg.role == "assistant" else "user"
        if isinstance(msg.content, str):
            contents.append(
                gtypes.Content(
                    parts=[gtypes.Part(text=msg.content)],
                    role=role,
                )
            )
        else:
            parts: list[gtypes.Part] = []
            for block in msg.content:
                if block.type == "text":
                    parts.append(gtypes.Part(text=block.text or ""))
                elif block.type == "tool_use":
                    parts.append(
                        gtypes.Part(
                            function_call=gtypes.FunctionCall(
                                name=block.tool_name or "",
                                args=block.tool_input or {},
                            ),
                        )
                    )
                elif block.type == "tool_result":
                    parts.append(
                        gtypes.Part(
                            function_response=gtypes.FunctionResponse(
                                name=block.tool_name or "",
                                response={"result": block.tool_result},
                            ),
                        )
                    )
            contents.append(gtypes.Content(parts=parts, role=role))
    return contents


def _to_google_tools(request: CompletionRequest) -> list[gtypes.Tool]:
    if not request.tools:
        return []
    declarations = [
        gtypes.FunctionDeclaration(
            name=t.name,
            description=t.description,
            parameters=t.input_schema or {"type": "object", "properties": {}},  # type: ignore[arg-type]
        )
        for t in request.tools
    ]
    return [gtypes.Tool(function_declarations=declarations)]


def _to_google_config(
    request: CompletionRequest,
) -> gtypes.GenerateContentConfig:
    kwargs: dict[str, Any] = {}
    if request.temperature is not None:
        kwargs["temperature"] = request.temperature
    if request.max_tokens is not None:
        kwargs["max_output_tokens"] = request.max_tokens
    if request.system:
        kwargs["system_instruction"] = request.system
    if request.tools:
        kwargs["tools"] = _to_google_tools(request)
    return gtypes.GenerateContentConfig(**kwargs)


def _from_google_response(raw: Any) -> CompletionResponse:
    blocks: list[ContentBlock] = []
    if raw.candidates:
        for part in raw.candidates[0].content.parts:
            if part.text:
                blocks.append(ContentBlock(type="text", text=part.text))
            elif part.function_call:
                blocks.append(
                    ContentBlock(
                        type="tool_use",
                        tool_name=part.function_call.name,
                        tool_input=dict(part.function_call.args or {}),
                    )
                )

    stop_reason: Any = "end_turn"
    if raw.candidates:
        fr = raw.candidates[0].finish_reason
        if fr and "MAX_TOKENS" in str(fr):
            stop_reason = "max_tokens"

    usage_meta = raw.usage_metadata
    usage = Usage(
        input_tokens=getattr(usage_meta, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage_meta, "candidates_token_count", 0) or 0,
    )

    return CompletionResponse(
        content=tuple(blocks),
        stop_reason=stop_reason,
        usage=usage,
        raw=raw,
    )
