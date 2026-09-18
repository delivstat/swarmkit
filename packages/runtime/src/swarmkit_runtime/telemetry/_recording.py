"""Record every model call's prompt and response into the local ring buffer.

`swarmkit debug` reads `.swarmkit/prompts.sqlite`; until 1.227.0 nothing wrote it, so the command
answered "No prompt ring buffer found" on every workspace that had ever run — the privacy-first
debugging story was a reader with no writer. This wraps each registered provider once, at runtime
construction, so all sixteen model-call sites are covered without touching any of them.

What is stored: the system prompt and the messages as the provider received them, the response
text, the model, the run and the agent (from the run scope and the node's context variable), and
a fresh span id. Nothing leaves the machine; retention is the ring buffer's (7 days).
"""

from __future__ import annotations

import contextlib
import itertools
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from swarmkit_runtime._run_scope import current_run_id
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol

logger = logging.getLogger("swarmkit.telemetry.recording")

_steps = itertools.count(1)


def _render_prompt(request: Any) -> str:
    parts: list[str] = []
    system = getattr(request, "system", None)
    if system:
        parts.append(f"[system]\n{system}")
    for msg in getattr(request, "messages", ()) or ():
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            blocks = []
            for block in content:
                text = getattr(block, "text", None)
                blocks.append(text if text else f"<{getattr(block, 'type', 'block')}>")
            content = "\n".join(blocks)
        parts.append(f"[{getattr(msg, 'role', '?')}]\n{content}")
    tools = getattr(request, "tools", None)
    if tools:
        parts.append("[tools] " + ", ".join(getattr(t, "name", "?") for t in tools))
    return "\n\n".join(parts)


def _render_response(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", ()) or ():
        kind = getattr(block, "type", "")
        if kind == "text" and getattr(block, "text", None):
            parts.append(str(block.text))
        elif kind == "tool_use":
            parts.append(
                f"<tool_use {getattr(block, 'tool_name', '?')} "
                f"{json.dumps(getattr(block, 'tool_input', None), default=str)}>"
            )
    return "\n".join(parts)


class RecordingProvider:
    """A provider that records each call to the ring buffer, then answers as the inner one does.

    Attribute access falls through to the wrapped provider, so `provider_id`, `supports`,
    `tokenize`, `enforces_response_schema` and anything vendor-specific keep working.
    """

    def __init__(self, inner: ModelProviderProtocol, prompts_db: Path) -> None:
        self._inner = inner
        self._prompts_db = prompts_db
        self._buffer: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def provider_id(self) -> str:
        return str(self._inner.provider_id)

    def supports(self, model: str) -> bool:
        return bool(self._inner.supports(model))

    def _buf(self) -> Any:
        if self._buffer is None:
            from swarmkit_runtime.telemetry._ring_buffer import PromptRingBuffer  # noqa: PLC0415

            self._prompts_db.parent.mkdir(parents=True, exist_ok=True)
            self._buffer = PromptRingBuffer(db_path=self._prompts_db)
        return self._buffer

    def _record(self, request: Any, response: Any) -> None:
        from swarmkit_runtime.langgraph_compiler._run_context import (  # noqa: PLC0415
            current_agent,
            current_parent_agent,
        )

        # Recording must never fail a call: a debugging aid that breaks the run it is meant to
        # explain is worse than none.
        with contextlib.suppress(Exception):
            self._buf().store(
                span_id=uuid.uuid4().hex,
                run_id=current_run_id() or "",
                agent_id=current_agent() or current_parent_agent() or "",
                step=next(_steps),
                prompt=_render_prompt(request),
                response=_render_response(response),
                model=str(getattr(request, "model", "")),
                metadata={"provider": self.provider_id},
            )

    async def complete(self, request: Any) -> Any:
        response = await self._inner.complete(request)
        self._record(request, response)
        return response

    async def stream(self, request: Any) -> Any:
        stream = getattr(self._inner, "stream", None)
        if stream is None:
            response = await self.complete(request)
            for block in getattr(response, "content", ()) or ():
                yield block
            return
        blocks: list[Any] = []
        async for block in stream(request):
            blocks.append(block)
            yield block

        class _Collected:
            content = tuple(blocks)

        self._record(request, _Collected())


def record_registry(registry: Any, workspace_root: Path) -> None:
    """Wrap every provider in *registry* so its calls land in the workspace's ring buffer."""
    prompts_db = workspace_root / ".swarmkit" / "prompts.sqlite"
    providers = getattr(registry, "_providers", None)
    if not isinstance(providers, dict):
        return
    for pid, provider in list(providers.items()):
        if isinstance(provider, RecordingProvider):
            continue
        providers[pid] = RecordingProvider(provider, prompts_db)
