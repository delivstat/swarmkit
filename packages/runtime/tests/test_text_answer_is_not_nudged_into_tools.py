"""An agent that holds skills and answers a question in text is not re-prompted to use them.

The "model returned text, nudging to use tools" retry fired for every text reply from any agent
that had a skill tool. A friendly assistant granted `summarize` and asked a two-sentence question
answered it, was told "call the tools now", and its final output became "I don't have a task that
requires a tool right now" — three model calls, and the answer was thrown away. The nudge is now
limited to the case it was written for: the model names a tool in prose instead of calling it.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.langgraph_compiler._delegation import _dispatch_response
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    Message,
    Usage,
)
from swarmkit_runtime.resolver import ResolvedAgent


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls += 1
        return _text("I would use the summarize tool for that.")


def _text(text: str) -> CompletionResponse:
    return CompletionResponse(
        content=(ContentBlock(type="text", text=text),),
        stop_reason="end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _agent() -> ResolvedAgent:
    return ResolvedAgent(
        id="assistant",
        role="root",
        model={"name": "m"},
        prompt={"system": "s"},
        skills=(),
        iam=None,
    )


@pytest.mark.asyncio
async def test_text_answer_is_not_nudged_into_tools() -> None:
    provider = _Provider()
    answer = "Writing things down keeps everyone aligned."
    result = await _dispatch_response(
        _text(answer),
        _agent(),
        "assistant",
        [Message(role="user", content="Why write things down?")],
        [_Tool("summarize")],
        "m",
        None,
        provider,  # type: ignore[arg-type]
        None,
        None,
        None,  # type: ignore[arg-type]
        "",
    )
    # On the unfixed code the provider was called twice more and the returned text was the
    # nudged reply, not the answer.
    assert provider.calls == 0
    assert isinstance(result, tuple)
    assert result[0].content[0].text == answer


@pytest.mark.asyncio
async def test_listing_the_tools_one_has_is_not_nudged_either() -> None:
    """ "I don't have a translate-text tool; I have summarize and get-weather" names two tools and
    is a complete, honest answer — it was nudged twice for naming them."""
    provider = _Provider()
    answer = "I don't have a translate-text tool. I have summarize and get-weather."
    result = await _dispatch_response(
        _text(answer),
        _agent(),
        "assistant",
        [Message(role="user", content="Translate this")],
        [_Tool("summarize"), _Tool("get-weather")],
        "m",
        None,
        provider,  # type: ignore[arg-type]
        None,
        None,
        None,  # type: ignore[arg-type]
        "",
    )
    assert provider.calls == 0
    assert isinstance(result, tuple) and result[0].content[0].text == answer


@pytest.mark.asyncio
async def test_describing_a_tool_call_is_still_nudged() -> None:
    provider = _Provider()
    result = await _dispatch_response(
        _text("I will call get-weather to check Tokyo."),
        _agent(),
        "assistant",
        [Message(role="user", content="Weather in Tokyo?")],
        [_Tool("get-weather")],
        "m",
        None,
        provider,  # type: ignore[arg-type]
        None,
        None,
        None,  # type: ignore[arg-type]
        "",
    )
    assert provider.calls >= 1
    assert isinstance(result, tuple)
