"""OllamaModelProvider — local inference via Ollama's HTTP API.

Uses ``httpx`` only (already a core dependency). No dedicated SDK.
Defaults to ``http://localhost:11434``; override via constructor
or ``extra.base_url`` on the request. Ollama exposes an
OpenAI-compatible ``/v1/chat/completions`` endpoint.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)

_DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaModelProvider:
    """ModelProvider for local Ollama models."""

    provider_id: str = "ollama"

    def __init__(self, *, base_url: str = _DEFAULT_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=120.0)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        from ._types import with_retry  # noqa: PLC0415

        payload = _to_ollama_payload(request)
        payload["stream"] = False

        async def _call() -> CompletionResponse:
            resp = await self._client.post("/api/chat", json=payload)
            resp.raise_for_status()
            return _from_ollama_response(resp.json())

        result: CompletionResponse = await with_retry(_call, label=f"ollama:{request.model}")
        return result

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        payload = _to_ollama_payload(request)
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

    def supports(self, model: str) -> bool:
        return True

    def tokenize(self, text: str, model: str) -> int | None:
        return None

    async def close(self) -> None:
        await self._client.aclose()


def _to_ollama_payload(request: CompletionRequest) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    for msg in request.messages:
        if isinstance(msg.content, str):
            messages.append({"role": msg.role, "content": msg.content})
        else:
            text_parts = [b.text for b in msg.content if b.type == "text" and b.text]
            images = [b.image_data for b in msg.content if b.type == "image" and b.image_data]
            entry: dict[str, Any] = {"role": msg.role, "content": " ".join(text_parts)}
            if images:
                entry["images"] = images
            messages.append(entry)

    payload: dict[str, Any] = {"model": request.model, "messages": messages}
    options: dict[str, Any] = {}
    if request.temperature is not None:
        options["temperature"] = request.temperature
    if request.max_tokens is not None:
        options["num_predict"] = request.max_tokens
    if options:
        payload["options"] = options
    if request.response_format is not None:
        rf_type = request.response_format.get("type", "")
        if rf_type in ("json_object", "json_schema"):
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
