"""Tests for ModelProvider types, MockModelProvider, registry, and provider
adapters (M2.5).

See ``design/details/model-provider-abstraction.md``.
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.model_providers import (
    AnthropicModelProvider,
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    GoogleModelProvider,
    Message,
    MockModelProvider,
    ProviderRegistry,
    Usage,
)

# ---- types --------------------------------------------------------------


def test_completion_request_is_frozen() -> None:
    req = CompletionRequest(
        model="test",
        messages=(Message(role="user", content="hi"),),
    )
    with pytest.raises(AttributeError):
        req.model = "other"  # type: ignore[misc]


def test_completion_response_is_frozen() -> None:
    resp = CompletionResponse(
        content=(ContentBlock(type="text", text="hi"),),
        stop_reason="end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    with pytest.raises(AttributeError):
        resp.stop_reason = "error"  # type: ignore[misc]


# ---- MockModelProvider ---------------------------------------------------


@pytest.mark.asyncio
async def test_mock_returns_default_response() -> None:
    mock = MockModelProvider()
    req = CompletionRequest(
        model="test-model",
        messages=(Message(role="user", content="hello"),),
    )
    resp = await mock.complete(req)
    assert resp.stop_reason == "end_turn"
    assert resp.content[0].text == "mock response"


@pytest.mark.asyncio
async def test_mock_returns_keyed_response() -> None:
    custom = CompletionResponse(
        content=(ContentBlock(type="text", text="custom reply"),),
        stop_reason="end_turn",
        usage=Usage(),
    )
    mock = MockModelProvider(responses={("model-a", "hi"): custom})
    req = CompletionRequest(
        model="model-a",
        messages=(Message(role="user", content="hi"),),
    )
    resp = await mock.complete(req)
    assert resp.content[0].text == "custom reply"


@pytest.mark.asyncio
async def test_mock_records_calls() -> None:
    mock = MockModelProvider()
    req = CompletionRequest(
        model="m",
        messages=(Message(role="user", content="x"),),
    )
    await mock.complete(req)
    await mock.complete(req)
    assert len(mock.calls) == 2


@pytest.mark.asyncio
async def test_mock_stream_yields_blocks() -> None:
    mock = MockModelProvider()
    req = CompletionRequest(
        model="m",
        messages=(Message(role="user", content="x"),),
    )
    blocks = [b async for b in mock.stream(req)]
    assert len(blocks) == 1
    assert blocks[0].text == "mock response"


def test_mock_supports_any_model() -> None:
    assert MockModelProvider().supports("anything") is True


def test_mock_tokenize_returns_word_count() -> None:
    assert MockModelProvider().tokenize("hello world", "m") == 2


# ---- ProviderRegistry ---------------------------------------------------


def test_registry_register_and_resolve() -> None:
    reg = ProviderRegistry()
    mock = MockModelProvider()
    reg.register(mock)
    assert reg.resolve("mock", "any-model") is mock


def test_registry_duplicate_id_raises() -> None:
    reg = ProviderRegistry()
    reg.register(MockModelProvider())
    with pytest.raises(ValueError, match="Duplicate"):
        reg.register(MockModelProvider())


def test_registry_resolve_missing_raises() -> None:
    reg = ProviderRegistry()
    with pytest.raises(LookupError, match="not registered"):
        reg.resolve("nonexistent", "model")


def test_registry_resolve_unsupported_model_raises() -> None:
    reg = ProviderRegistry()
    reg.register(AnthropicModelProvider(api_key="dummy"))
    with pytest.raises(LookupError, match="does not support"):
        reg.resolve("anthropic", "gpt-4o")


def test_registry_provider_ids() -> None:
    reg = ProviderRegistry()
    reg.register(MockModelProvider())
    reg.register(AnthropicModelProvider(api_key="dummy"))
    reg.register(GoogleModelProvider(api_key="dummy"))
    assert reg.provider_ids == ["anthropic", "google", "mock"]


# ---- AnthropicModelProvider (unit-level) ---------------------------------


def test_anthropic_supports_claude_models() -> None:
    provider = AnthropicModelProvider(api_key="dummy")
    assert provider.supports("claude-sonnet-4-6") is True
    assert provider.supports("claude-opus-4-7") is True
    assert provider.supports("gpt-4o") is False
    assert provider.supports("gemini-2.5-flash") is False


# ---- GoogleModelProvider (unit-level) ------------------------------------


def test_google_supports_gemini_models() -> None:
    provider = GoogleModelProvider(api_key="dummy")
    assert provider.supports("gemini-2.5-flash") is True
    assert provider.supports("gemini-2.5-pro") is True
    assert provider.supports("claude-sonnet-4-6") is False
    assert provider.supports("gpt-4o") is False


# ---- integration tests (gated on API keys) ------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anthropic_live_completion() -> None:
    provider = AnthropicModelProvider()
    resp = await provider.complete(
        CompletionRequest(
            model="claude-sonnet-4-6",
            messages=(Message(role="user", content="Say 'ready' and nothing else."),),
            max_tokens=16,
        )
    )
    assert resp.stop_reason in ("end_turn", "max_tokens")
    assert any(b.text for b in resp.content)
    assert resp.usage.input_tokens > 0
    assert resp.usage.output_tokens > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_google_live_completion() -> None:
    provider = GoogleModelProvider()
    resp = await provider.complete(
        CompletionRequest(
            model="gemini-2.5-flash",
            messages=(Message(role="user", content="Say 'ready' and nothing else."),),
            max_tokens=16,
        )
    )
    assert resp.stop_reason in ("end_turn", "max_tokens")
    assert any(b.text for b in resp.content)
    assert resp.usage.input_tokens > 0


# ---- cross-provider: different models per agent (design §10.2) ----------


@pytest.mark.asyncio
async def test_registry_resolves_different_providers_per_agent() -> None:
    """Proves the topology can assign different models to different agents.
    The registry resolves each to the correct provider instance.
    """
    reg = ProviderRegistry()
    mock_anthropic = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text="claude says hi"),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_anthropic.provider_id = "anthropic"

    mock_google = MockModelProvider(
        default_response=CompletionResponse(
            content=(ContentBlock(type="text", text="gemini says hi"),),
            stop_reason="end_turn",
            usage=Usage(),
        )
    )
    mock_google.provider_id = "google"

    reg.register(mock_anthropic)
    reg.register(mock_google)

    # Root agent uses anthropic
    root_provider = reg.resolve("anthropic", "claude-opus-4-7")
    root_resp = await root_provider.complete(
        CompletionRequest(
            model="claude-opus-4-7",
            messages=(Message(role="user", content="hi"),),
        )
    )
    assert root_resp.content[0].text == "claude says hi"

    # Worker uses google
    worker_provider = reg.resolve("google", "gemini-2.5-flash")
    worker_resp = await worker_provider.complete(
        CompletionRequest(
            model="gemini-2.5-flash",
            messages=(Message(role="user", content="hi"),),
        )
    )
    assert worker_resp.content[0].text == "gemini says hi"
