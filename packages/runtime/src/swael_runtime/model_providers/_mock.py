"""MockModelProvider — deterministic, test-only.

Always importable (no SDK dependency). Returns configurable canned
responses keyed on ``(model, first_message_content)``, or a default
response if no match is found.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ._types import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)

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
        self._calls.append(request)
        key = self._key(request)
        return self._responses.get(key, self._default)

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
