"""OllamaModelProvider — the ``ollama`` family; Ollama's native ``/api/chat`` over ``httpx``.

Uses ``httpx`` only (already a core dependency). No dedicated SDK. Defaults to
``http://localhost:11434``; a provider YAML with ``extends: ollama`` points it anywhere that speaks
the same API — rkllama on a Rockchip NPU, for one.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from ._family import FamilyBase
from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)

_DEFAULT_BASE_URL = "http://localhost:11434"

# Models that need special handling for tool calling.
# Gemma models use <think> blocks that confuse the Ollama tool parser,
# and expect role="tool_responses" instead of role="tool".
_THINKING_MODEL_FAMILIES = ("gemma",)

# Ollama keys that live at the ROOT of the /api/chat payload rather than inside its ``options``
# object. They arrive through ``model.options`` like every other provider-native knob — that is the
# only passthrough an archetype has — so they must be lifted back out before the call. Left nested,
# Ollama silently ignores them: ``options.think`` is not a field it reads, so the agent reasons
# anyway and the author sees a setting that is accepted everywhere and honoured nowhere.
# The default; a provider YAML overrides it with ``options.lift_to_root``.
_OLLAMA_TOP_LEVEL_OPTIONS = ("think", "keep_alive")


def _is_thinking_model(model: str) -> bool:
    """Check if a model belongs to a family that uses thinking tokens."""
    model_lower = model.lower()
    return any(family in model_lower for family in _THINKING_MODEL_FAMILIES)


class OllamaModelProvider(FamilyBase):
    """ModelProvider for local Ollama models, and for any server speaking Ollama's API."""

    provider_id: str = "ollama"

    #: Ollama compiles ``format`` into a decoding grammar.
    enforces_response_schema: bool = True

    def __init__(
        self,
        *,
        base_url: str | None = _DEFAULT_BASE_URL,
        api_key: str | None = None,
        provider_id: str | None = None,
        auth: Any = None,
        headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, Any] | None = None,
        lift_to_root: tuple[str, ...] | None = None,
        model_pattern: str | None = None,
        accept_any_model: bool = False,
        capabilities: Mapping[str, bool] | None = None,
    ) -> None:
        self._configure(
            provider_id=provider_id,
            model_pattern=model_pattern,
            accept_any_model=accept_any_model,
            capabilities=capabilities,
            default_pattern=None,
            default_accept_any=True,
            headers=headers,
        )
        self._lift = tuple(lift_to_root) if lift_to_root is not None else _OLLAMA_TOP_LEVEL_OPTIONS
        self._extra_body: dict[str, Any] = dict(extra_body or {})
        request_headers = dict(self.extra_headers)
        if auth is not None and auth.api_key_env is not None and api_key:
            # Ollama itself has no auth; a fronting proxy or a compatible server may.
            request_headers[auth.header] = f"{auth.scheme} {api_key}".strip()
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=300.0, headers=request_headers or None
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        from ._types import with_retry  # noqa: PLC0415

        self._check(request)
        payload = self._payload(request)
        payload["stream"] = False

        async def _call() -> CompletionResponse:
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            return _from_ollama_response(resp.json())

        result: CompletionResponse = await with_retry(_call, label=f"ollama:{request.model}")
        return result

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        self._check(request)
        self._check_stream()
        payload = self._payload(request)
        payload["stream"] = True
        async with self._client.stream("POST", "/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                if msg.get("content"):
                    yield ContentBlock(type="text", text=msg["content"])

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload = _to_ollama_payload(
            request, lift_to_root=self._lift, structured_output=self._structured_output
        )
        payload.update(self._extra_body)
        return payload

    def tokenize(self, text: str, model: str) -> int | None:
        return None

    async def close(self) -> None:
        await self._client.aclose()


def _build_messages(request: CompletionRequest, *, remap_tool_role: bool) -> list[dict[str, Any]]:
    """Convert request messages to Ollama's format."""
    messages: list[dict[str, Any]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    for msg in request.messages:
        role: str = msg.role
        if remap_tool_role and role == "tool":
            role = "tool_responses"
        if isinstance(msg.content, str):
            messages.append({"role": role, "content": msg.content})
        else:
            text_parts = [b.text for b in msg.content if b.type == "text" and b.text]
            images = [b.image_data for b in msg.content if b.type == "image" and b.image_data]
            entry: dict[str, Any] = {"role": role, "content": " ".join(text_parts)}
            if images:
                entry["images"] = images
            messages.append(entry)
    return messages


def _to_ollama_payload(
    request: CompletionRequest,
    *,
    lift_to_root: tuple[str, ...] = _OLLAMA_TOP_LEVEL_OPTIONS,
    structured_output: bool = True,
) -> dict[str, Any]:
    is_gemma = _is_thinking_model(request.model)
    # Gemma expects "tool_responses" instead of "tool" for tool results.
    # Without this mapping, Gemma loops indefinitely re-calling the same tool.
    messages = _build_messages(request, remap_tool_role=is_gemma)
    payload: dict[str, Any] = {"model": request.model, "messages": messages}
    options: dict[str, Any] = {}
    if request.temperature is not None:
        options["temperature"] = request.temperature
    if request.max_tokens is not None:
        options["num_predict"] = request.max_tokens
    # Generic per-model options (num_ctx, repeat_penalty, top_k, ...) fold
    # straight into Ollama's native options object, overriding the first-class
    # fields above on conflict. num_ctx matters here: Ollama defaults to a
    # small context (2048) and silently truncates longer prompts, which a
    # large system prompt + tool schemas can exceed.
    if request.options:
        options.update(request.options)

    # Gemma models use <think> blocks that interfere with Ollama's tool-call
    # parser — the parser fails to piece together tool-call JSON across
    # thinking chunks. Disabling thinking forces the model to emit tool
    # calls directly, which Ollama can parse reliably.
    if is_gemma:
        payload["think"] = False

    # Lift the root-level keys out of ``options`` (see _OLLAMA_TOP_LEVEL_OPTIONS). This runs AFTER
    # the Gemma default so an explicit setting wins: the default is there because Gemma's thinking
    # breaks tool-call parsing, which makes it a good default and a bad law — a Gemma planner that
    # wants reasoning and emits no tool calls should be able to ask for it.
    for key in lift_to_root:
        if key in options:
            payload[key] = options.pop(key)

    if options:
        payload["options"] = options

    if request.response_format is not None and structured_output:
        rf_type = request.response_format.get("type", "")
        if rf_type == "json_schema":
            # Ollama structured outputs: pass the JSON schema as ``format`` so
            # decoding is constrained to the schema, not just "valid JSON".
            schema = (request.response_format.get("json_schema") or {}).get("schema")
            payload["format"] = schema if schema else "json"
        elif rf_type == "json_object":
            payload["format"] = "json"
    if request.tools:
        payload["tools"] = [
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
    return payload


def _from_ollama_response(data: dict[str, Any]) -> CompletionResponse:
    msg = data.get("message", {})
    content_text = msg.get("content", "")
    blocks: list[ContentBlock] = []
    if content_text:
        blocks.append(ContentBlock(type="text", text=content_text))

    for tc in msg.get("tool_calls", []):
        fn = tc.get("function", {})
        blocks.append(
            ContentBlock(
                type="tool_use",
                tool_use_id=tc.get("id", ""),
                tool_name=fn.get("name", ""),
                tool_input=fn.get("arguments"),
            )
        )

    stop_reason: Any = "end_turn"
    if data.get("done_reason") == "length":
        stop_reason = "max_tokens"
    if msg.get("tool_calls"):
        stop_reason = "tool_use"

    eval_count = data.get("eval_count", 0) or 0
    prompt_eval_count = data.get("prompt_eval_count", 0) or 0

    return CompletionResponse(
        content=tuple(blocks),
        stop_reason=stop_reason,
        usage=Usage(input_tokens=prompt_eval_count, output_tokens=eval_count),
        raw=data,
    )
