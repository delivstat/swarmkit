"""The input-request classifier (executor-input-escalation-plan.md, §6.3 PR1)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from swarmkit_runtime.executors import ExecInputRequested
from swarmkit_runtime.langgraph_compiler._input_classifier import (
    classify_input_request,
    should_classify,
)
from swarmkit_runtime.model_providers import (
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    Usage,
)


class _FakeModel:
    """Returns a preset JSON classification, or raises to exercise the fail-open path."""

    provider_id = "fake"

    def __init__(self, payload: object, *, raise_exc: Exception | None = None) -> None:
        self._payload = payload
        self._raise = raise_exc
        self.calls: list[CompletionRequest] = []

    def supports(self, model: str) -> bool:
        return True

    async def complete(self, request: Any) -> CompletionResponse:
        self.calls.append(request)
        if self._raise is not None:
            raise self._raise
        text = self._payload if isinstance(self._payload, str) else json.dumps(self._payload)
        return CompletionResponse(
            content=(ContentBlock(type="text", text=text),),
            stop_reason="end_turn",
            usage=Usage(),
        )


def test_pre_filter_skips_when_an_artifact_was_produced() -> None:
    # the harness did the work → it wasn't asking → no classifier call
    assert should_classify(artifact_present=False) is True
    assert should_classify(artifact_present=True) is False


@pytest.mark.asyncio
async def test_detects_a_question_and_extracts_options() -> None:
    model = _FakeModel(
        {
            "is_request": True,
            "question": "Which cache backend should I use?",
            "options": ["redis", "memcached"],
            "free_text_allowed": False,
            "question_class": "implementation-choice",
        }
    )
    result = await classify_input_request(
        "I can use redis or memcached for the cache. Which do you want?",
        model_provider=model,
        model="small",
    )
    assert isinstance(result, ExecInputRequested)
    assert result.question == "Which cache backend should I use?"
    assert result.options == ("redis", "memcached")
    assert result.free_text_allowed is False
    assert result.question_class == "implementation-choice"
    # it used structured output
    assert model.calls[0].response_format is not None


@pytest.mark.asyncio
async def test_not_a_request_returns_none() -> None:
    model = _FakeModel({"is_request": False})
    result = await classify_input_request(
        "Done — added the endpoint and tests pass.", model_provider=model, model="small"
    )
    assert result is None


@pytest.mark.asyncio
async def test_empty_message_skips_the_model_call() -> None:
    model = _FakeModel({"is_request": True, "question": "x"})
    assert await classify_input_request("   ", model_provider=model, model="small") is None
    assert model.calls == []  # never called


@pytest.mark.asyncio
async def test_fails_open_on_bad_output() -> None:
    # unparseable model output must never dead-end a run → None
    bad = _FakeModel("not json at all")
    assert await classify_input_request("hmm?", model_provider=bad, model="small") is None
    # a raising provider likewise
    boom = _FakeModel({}, raise_exc=RuntimeError("model down"))
    assert await classify_input_request("hmm?", model_provider=boom, model="small") is None
