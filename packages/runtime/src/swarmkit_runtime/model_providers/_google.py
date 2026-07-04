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
    NON_NATIVE_OPTIONS,
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
    apply_options,
)

# GenerateContentConfig accepts top_k/top_p/seed (unlike the OpenAI chat API); keep top_k.
_DROP = NON_NATIVE_OPTIONS - frozenset({"top_k"})

_GEMINI_PREFIXES = ("gemini-",)


class GoogleModelProvider:
    """ModelProvider for Google's Gemini models via google-genai."""

    provider_id: str = "google"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        self._client = genai.Client(api_key=api_key, **kwargs)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        from ._types import with_retry  # noqa: PLC0415

        contents = _to_google_contents(request)
        config = _to_google_config(request)

        raw = await with_retry(
            lambda: self._client.aio.models.generate_content(
                model=request.model,
                contents=contents,
                config=config,
            ),
            label=f"google:{request.model}",
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
    # The system prompt goes through Gemini's native system_instruction (see
    # _to_google_config); do NOT also inject it as a fabricated user/model turn here, or the
    # model receives it twice (wasted tokens + a fake dialogue turn that can confuse it).
    contents: list[gtypes.Content] = []

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
                elif block.type == "image" and block.image_data:
                    import base64  # noqa: PLC0415

                    parts.append(
                        gtypes.Part(
                            inline_data=gtypes.Blob(
                                mime_type=block.image_media_type or "image/png",
                                data=base64.standard_b64decode(block.image_data),
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
    if request.response_format is not None:
        rf_type = request.response_format.get("type", "")
        if rf_type in ("json_object", "json_schema"):
            kwargs["response_mime_type"] = "application/json"
        if rf_type == "json_schema" and "json_schema" in request.response_format:
            kwargs["response_schema"] = request.response_format["json_schema"].get("schema")
    if request.tools:
        kwargs["tools"] = _to_google_tools(request)
        mode = gtypes.FunctionCallingConfigMode.AUTO
        if request.extra and request.extra.get("tool_choice") == "required":
            mode = gtypes.FunctionCallingConfigMode.ANY
        kwargs["tool_config"] = gtypes.ToolConfig(
            function_calling_config=gtypes.FunctionCallingConfig(mode=mode),
        )
        # Gemini thinking models can interfere with tool calling —
        # the thinking phase may time out or generate incomplete tool
        # call blocks. Disable thinking when tools are present.
        kwargs["thinking_config"] = gtypes.ThinkingConfig(thinking_budget=0)
    # Per-model options become GenerateContentConfig fields (overriding the first-class
    # settings above), minus Ollama-only knobs GenerateContentConfig's validation rejects
    # (num_ctx, keep_alive, mirostat, …). ``extra`` is not folded here — for Google it
    # carries tool_choice semantics, handled above.
    apply_options(kwargs, request.options, None, drop=_DROP)
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
