"""MockModelProvider — deterministic, test-only.

Always importable (no SDK dependency). Returns configurable canned
responses keyed on ``(model, first_message_content)``, or a default
response if no match is found.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import AsyncIterator

from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)


async def _mock_latency() -> None:
    """Simulate a model call's wall time so a load test measures the runtime under realistic
    concurrency, not an instant-return mock (SWARMKIT_MOCK_LATENCY_MS, load-and-scale.md).

    ``SWARMKIT_MOCK_LATENCY_MS`` is the base sleep in milliseconds (0/unset = instant, today's
    behaviour). ``SWARMKIT_MOCK_LATENCY_JITTER_MS`` adds a uniform +/- jitter so concurrent calls
    do not all wake on the same tick — real model variance, deterministic in aggregate.
    """
    try:
        base = float(os.environ.get("SWARMKIT_MOCK_LATENCY_MS", "0") or 0)
    except ValueError:
        base = 0.0
    if base <= 0:
        return
    try:
        jitter = float(os.environ.get("SWARMKIT_MOCK_LATENCY_JITTER_MS", "0") or 0)
    except ValueError:
        jitter = 0.0
    delay = base + (random.uniform(-jitter, jitter) if jitter > 0 else 0.0)
    await asyncio.sleep(max(0.0, delay) / 1000.0)


_DEFAULT_RESPONSE = CompletionResponse(
    content=(ContentBlock(type="text", text="mock response"),),
    stop_reason="end_turn",
    usage=Usage(input_tokens=10, output_tokens=5),
)


class MockModelProvider:
    """Deterministic model provider for unit tests.

    ``responses`` maps ``(model, first_user_content)`` → ``CompletionResponse``.
    Unmatched requests return ``default_response``.
    """

    provider_id: str = "mock"

    #: The mock returns canned text; nothing constrains it.
    enforces_response_schema: bool = False

    def __init__(
        self,
        *,
        responses: dict[tuple[str, str], CompletionResponse] | None = None,
        default_response: CompletionResponse = _DEFAULT_RESPONSE,
    ) -> None:
        self._responses = responses or {}
        self._default = default_response
        self._calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        await _mock_latency()
        self._calls.append(request)
        key = self._key(request)
        if key in self._responses:
            return self._responses[key]
        delegation = self._delegation(request)
        return delegation if delegation is not None else self._default

    @staticmethod
    def _delegation(request: CompletionRequest) -> CompletionResponse | None:
        """With ``SWARMKIT_MOCK_DELEGATE=1``, a coordinator delegates once to every child.

        Off by default, so nothing that counts calls changes. On, a `swarmkit run` on the mock
        provider traverses a multi-agent topology instead of stopping at the root's canned
        text — which is what a failure-path or resume test needs: children that actually run
        (test_kill9_recovery.py).
        """
        if os.environ.get("SWARMKIT_MOCK_DELEGATE") != "1" or not request.tools:
            return None
        if any(
            m.role == "tool"
            or (
                isinstance(m.content, list)
                and any(getattr(b, "type", "") == "tool_result" for b in m.content)
            )
            for m in request.messages
        ):
            return None  # the children have answered; the default text is the synthesis
        delegates = [t.name for t in request.tools if t.name.startswith("delegate_to_")]
        if not delegates:
            return None
        task = next(
            (
                m.content
                for m in request.messages
                if m.role == "user" and isinstance(m.content, str)
            ),
            "do the work",
        )
        return CompletionResponse(
            content=tuple(
                ContentBlock(
                    type="tool_use",
                    tool_name=name,
                    tool_use_id=f"call_{i}",
                    tool_input={"task": task},
                )
                for i, name in enumerate(delegates)
            ),
            stop_reason="tool_use",
            usage=Usage(input_tokens=10, output_tokens=5),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        response = await self.complete(request)
        for block in response.content:
            yield block

    def supports(self, model: str) -> bool:
        return True

    def tokenize(self, text: str, model: str) -> int | None:
        return len(text.split())

    @property
    def calls(self) -> list[CompletionRequest]:
        return list(self._calls)

    @staticmethod
    def _key(request: CompletionRequest) -> tuple[str, str]:
        for msg in request.messages:
            if msg.role == "user":
                content = msg.content if isinstance(msg.content, str) else ""
                return (request.model, content)
        return (request.model, "")
