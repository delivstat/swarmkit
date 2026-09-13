"""OpenAIModelProvider — the ``openai-compatible`` family; wraps the ``openai`` SDK.

Only this file imports ``openai``. Parameterised by a provider YAML (``_declarative.py``): base
URL, auth header, static headers, ``extra_body`` quirks, the model catalogue. Unparameterised it
is OpenAI itself, exactly as before.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import openai

from ._family import FamilyBase
from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
    apply_options,
)

#: OpenAI's own catalogue. A YAML supplies its own ``models.pattern`` or ``accept_any``.
_OPENAI_PATTERN = r"^(gpt-|o1-|o3-|o4-)"


def _dump_tool_args(value: Any) -> str:
    """OpenAI expects ``function.arguments`` as a JSON **string**. A dict must be
    ``json.dumps``-ed (not ``str()``-ed, which emits invalid single-quoted JSON and
    corrupts a replayed tool call); a value already a string is passed through."""
    if isinstance(value, str):
        return value
    return json.dumps(value or {})


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    """The OpenAI SDK returns ``function.arguments`` as a JSON string, but
    ``ContentBlock.tool_input`` is typed ``dict``. Parse it here so downstream code
    doesn't have to string-guard; empty dict on malformed/non-object JSON."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class OpenAIModelProvider(FamilyBase):
    """ModelProvider for OpenAI's GPT / o-series models, and for every server that speaks the
    chat-completions API — which is what a provider YAML with ``extends: openai-compatible``
    turns this into."""

    provider_id: str = "openai"

    #: OpenAI Structured Outputs constrains generation to the json_schema.
    enforces_response_schema: bool = True

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
            default_pattern=_OPENAI_PATTERN,
            default_accept_any=False,
            headers=headers,
        )
        # A body the base API does not define but this server wants on every call — OpenRouter's
        # ``usage: {include: true}`` (per-call cost, read back as ``raw.usage.cost``). Declared by
        # the YAML; the base OpenAI API rejects it, so it is never a default.
        self._extra_body: dict[str, Any] = dict(extra_body or {})
        default_headers = dict(self.extra_headers)
        if auth is not None and auth.api_key_env is None:
            # No auth — a local runtime. The SDK insists on *some* key (it reads OPENAI_API_KEY
            # otherwise, and raises when that is unset too), so it gets a placeholder the server
            # never reads.
            api_key = api_key or "no-auth"
        elif auth is not None and not auth.is_bearer:
            # The key travels in a different header (Azure's ``api-key``). The SDK still sends
            # its own Authorization: Bearer, with the placeholder.
            default_headers[auth.header] = f"{auth.scheme} {api_key or ''}".strip()
            api_key = "in-header"
        if default_headers:
            kwargs.setdefault("default_headers", default_headers)
        if base_url is not None:
            kwargs.setdefault("base_url", base_url)
        self._client = openai.AsyncOpenAI(api_key=api_key, **kwargs)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        from ._types import with_retry  # noqa: PLC0415

        self._check(request)
        kwargs = _to_openai_kwargs(request, structured_output=self._structured_output)
        if self._extra_body:
            extra_body = dict(kwargs.get("extra_body") or {})
            extra_body.update(self._extra_body)
            kwargs["extra_body"] = extra_body
        raw = await with_retry(
            lambda: self._client.chat.completions.create(**kwargs),
            label=f"openai:{request.model}",
        )
        return _from_openai_response(raw)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        self._check(request)
        self._check_stream()
        kwargs = _to_openai_kwargs(request, structured_output=self._structured_output)
        kwargs["stream"] = True
        async for chunk in await self._client.chat.completions.create(**kwargs):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield ContentBlock(type="text", text=delta.content)

    def tokenize(self, text: str, model: str) -> int | None:
        return None


def _to_openai_kwargs(
    request: CompletionRequest, *, structured_output: bool = True
) -> dict[str, Any]:
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
    if request.response_format is not None and structured_output:
        kwargs["response_format"] = request.response_format
    # Per-model options (top_p, frequency_penalty, seed, ...) minus Ollama-only knobs the
    # OpenAI SDK would reject; runtime ``extra`` applied last (never filtered).
    return apply_options(kwargs, request.options, request.extra)


def _build_openai_messages(request: CompletionRequest) -> list[dict[str, Any]]:  # noqa: PLR0912
    messages: list[dict[str, Any]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    for msg in request.messages:
        if isinstance(msg.content, str):
            messages.append({"role": msg.role, "content": msg.content})
        else:
            parts: list[dict[str, Any]] = []
            tool_calls: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            for block in msg.content:
                if block.type == "text":
                    parts.append({"type": "text", "text": block.text or ""})
                elif block.type == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.tool_use_id or "",
                            "type": "function",
                            "function": {
                                "name": block.tool_name or "",
                                "arguments": _dump_tool_args(block.tool_input),
                            },
                        }
                    )
                elif block.type == "tool_result":
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.tool_use_id or "",
                            "content": (
                                block.tool_result
                                if isinstance(block.tool_result, str)
                                else json.dumps(block.tool_result)
                                if block.tool_result
                                else ""
                            ),
                        }
                    )
                elif block.type == "image" and block.image_data:
                    mt = block.image_media_type or "image/png"
                    data_url = f"data:{mt};base64,{block.image_data}"
                    parts.append({"type": "image_url", "image_url": {"url": data_url}})
            if tool_calls:
                assistant_msg: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
                if parts:
                    assistant_msg["content"] = parts
                messages.append(assistant_msg)
            elif parts:
                messages.append({"role": msg.role, "content": parts})
            for tr in tool_results:
                messages.append(tr)
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
                    tool_input=_parse_tool_args(tc.function.arguments),
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
        # OpenRouter returns per-call cost here (USD) when asked (see `complete`); other
        # OpenAI-compat providers omit it → 0.0, and a price table fills it in later (PR 2).
        cost_usd=float(getattr(raw.usage, "cost", 0.0) or 0.0),
    )

    return CompletionResponse(
        content=tuple(blocks),
        stop_reason=stop_reason,
        usage=usage,
        raw=raw,
    )
