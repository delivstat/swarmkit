"""Tests for ModelProvider types, MockModelProvider, registry, and provider
adapters (M2.5).

See ``design/details/model-provider-abstraction.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.model_providers import (
    AnthropicModelProvider,
    CompletionRequest,
    CompletionResponse,
    ContentBlock,
    GoogleModelProvider,
    Message,
    MockModelProvider,
    OllamaModelProvider,
    OpenAIModelProvider,
    ProviderRegistry,
    Usage,
    image_block,
)
from swarmkit_runtime.model_providers._anthropic import _to_anthropic_messages
from swarmkit_runtime.model_providers._google import _to_google_config
from swarmkit_runtime.model_providers._ollama import _to_ollama_payload
from swarmkit_runtime.model_providers._openai import (
    _build_openai_messages,
    _to_openai_kwargs,
)

# ---- types --------------------------------------------------------------


def test_completion_request_is_frozen() -> None:
    req = CompletionRequest(
        model="test",
        messages=(Message(role="user", content="hi"),),
    )
    with pytest.raises(AttributeError):
        req.model = "other"  # type: ignore[misc,unused-ignore]


def test_completion_response_is_frozen() -> None:
    resp = CompletionResponse(
        content=(ContentBlock(type="text", text="hi"),),
        stop_reason="end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    with pytest.raises(AttributeError):
        resp.stop_reason = "error"  # type: ignore[misc,unused-ignore]


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


# ---- OpenAIModelProvider (unit-level) ------------------------------------


def test_openai_supports_gpt_and_o_models() -> None:
    provider = OpenAIModelProvider(api_key="dummy")
    assert provider.supports("gpt-4o") is True
    assert provider.supports("gpt-4o-mini") is True
    assert provider.supports("o1-preview") is True
    assert provider.supports("o3-mini") is True
    assert provider.supports("claude-sonnet-4-6") is False
    assert provider.supports("gemini-2.5-flash") is False


# ---- OllamaModelProvider (unit-level) ------------------------------------


def test_ollama_supports_any_model() -> None:
    provider = OllamaModelProvider()
    assert provider.supports("llama3.1") is True
    assert provider.supports("mistral") is True
    assert provider.supports("any-local-model") is True


def test_registry_all_five_providers() -> None:
    reg = ProviderRegistry()
    reg.register(MockModelProvider())
    reg.register(AnthropicModelProvider(api_key="dummy"))
    reg.register(GoogleModelProvider(api_key="dummy"))
    reg.register(OpenAIModelProvider(api_key="dummy"))
    reg.register(OllamaModelProvider())
    assert reg.provider_ids == ["anthropic", "google", "mock", "ollama", "openai"]
    assert len(reg) == 5


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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_live_completion() -> None:
    provider = OpenAIModelProvider()
    resp = await provider.complete(
        CompletionRequest(
            model="gpt-4o-mini",
            messages=(Message(role="user", content="Say 'ready' and nothing else."),),
            max_tokens=16,
        )
    )
    assert resp.stop_reason in ("end_turn", "max_tokens")
    assert any(b.text for b in resp.content)
    assert resp.usage.input_tokens > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ollama_live_completion() -> None:
    provider = OllamaModelProvider()
    resp = await provider.complete(
        CompletionRequest(
            model="llama3.2",
            messages=(Message(role="user", content="Say 'ready' and nothing else."),),
            max_tokens=16,
        )
    )
    assert resp.stop_reason in ("end_turn", "max_tokens")
    assert any(b.text for b in resp.content)


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


# ---- multimodal / image support -------------------------------------------


def test_content_block_image_fields() -> None:
    block = ContentBlock(
        type="image",
        image_data="iVBORw0KGgoAAAA...",
        image_media_type="image/png",
    )
    assert block.type == "image"
    assert block.image_data == "iVBORw0KGgoAAAA..."
    assert block.image_media_type == "image/png"


def test_image_block_from_file(tmp_path: Path) -> None:
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake png data")
    block = image_block(str(img))
    assert block.type == "image"
    assert block.image_media_type == "image/png"
    assert block.image_data is not None
    assert len(block.image_data) > 0


def test_image_block_jpeg(tmp_path: Path) -> None:
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff fake jpeg")
    block = image_block(str(img))
    assert block.image_media_type == "image/jpeg"


def test_image_block_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        image_block("/tmp/nonexistent_image_12345.png")


def test_image_block_unsupported_format(tmp_path: Path) -> None:
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="Unsupported image format"):
        image_block(str(f))


def test_message_with_image_content() -> None:
    """Messages can mix text and image content blocks."""
    msg = Message(
        role="user",
        content=[
            ContentBlock(type="text", text="What's in this diagram?"),
            ContentBlock(
                type="image",
                image_data="base64data",
                image_media_type="image/png",
            ),
        ],
    )
    assert not isinstance(msg.content, str)
    assert len(msg.content) == 2
    assert msg.content[0].type == "text"
    assert msg.content[1].type == "image"


def test_anthropic_image_translation() -> None:
    """Anthropic adapter translates image blocks to Anthropic's format."""
    request = CompletionRequest(
        model="claude-sonnet-4-6",
        messages=[
            Message(
                role="user",
                content=[
                    ContentBlock(type="text", text="Describe this"),
                    ContentBlock(
                        type="image",
                        image_data="aW1hZ2VkYXRh",
                        image_media_type="image/png",
                    ),
                ],
            ),
        ],
    )
    messages = _to_anthropic_messages(request)
    assert len(messages) == 1
    blocks = messages[0]["content"]
    assert blocks[0] == {"type": "text", "text": "Describe this"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["type"] == "base64"
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert blocks[1]["source"]["data"] == "aW1hZ2VkYXRh"


def test_openai_image_translation() -> None:
    """OpenAI adapter translates image blocks to data URL format."""
    request = CompletionRequest(
        model="gpt-4o",
        messages=[
            Message(
                role="user",
                content=[
                    ContentBlock(type="text", text="What is this?"),
                    ContentBlock(
                        type="image",
                        image_data="aW1hZ2VkYXRh",
                        image_media_type="image/jpeg",
                    ),
                ],
            ),
        ],
    )
    messages = _build_openai_messages(request)
    assert len(messages) == 1
    parts = messages[0]["content"]
    assert parts[0] == {"type": "text", "text": "What is this?"}
    assert parts[1]["type"] == "image_url"
    assert "data:image/jpeg;base64,aW1hZ2VkYXRh" in parts[1]["image_url"]["url"]


# ---- schema-constrained response_format ----------------------------------
#
# A worker with an output_schema produces a json_schema response_format
# (see _build_completion_request). Each provider must translate that into its
# native schema-constrained decoding control rather than a generic "valid JSON"
# flag, so small local models are held to the schema shape.

_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
    "additionalProperties": False,
}

_JSON_SCHEMA_RF = {
    "type": "json_schema",
    "json_schema": {"name": "agent_output", "schema": _OUTPUT_SCHEMA},
}


def _req(response_format: dict[str, Any] | None) -> CompletionRequest:
    return CompletionRequest(
        model="m",
        messages=(Message(role="user", content="hi"),),
        response_format=response_format,
    )


def test_ollama_json_schema_sets_format_to_schema() -> None:
    payload = _to_ollama_payload(_req(_JSON_SCHEMA_RF))
    # Ollama constrains decoding when ``format`` is the schema object itself.
    assert payload["format"] == _OUTPUT_SCHEMA


def test_ollama_json_object_stays_json_mode() -> None:
    payload = _to_ollama_payload(_req({"type": "json_object"}))
    assert payload["format"] == "json"


def test_ollama_no_response_format_omits_format() -> None:
    payload = _to_ollama_payload(_req(None))
    assert "format" not in payload


def test_openai_passes_json_schema_through() -> None:
    kwargs = _to_openai_kwargs(_req(_JSON_SCHEMA_RF))
    # OpenAI / OpenRouter consume the json_schema response_format natively.
    assert kwargs["response_format"] == _JSON_SCHEMA_RF


def test_openai_drops_ollama_only_options() -> None:
    # Options authored for Ollama (num_ctx, num_gpu, keep_alive, top_k, ...) must NOT
    # reach the OpenAI SDK's create() — it rejects unknown kwargs, which broke routing
    # when an Ollama-tuned topology was pointed at OpenRouter (num_ctx 8192).
    req = CompletionRequest(
        model="moonshotai/kimi-k2.6",
        messages=(Message(role="user", content="hi"),),
        options={
            "num_ctx": 8192,
            "num_gpu": 0,
            "keep_alive": -1,
            "top_k": 40,
            "top_p": 0.9,  # genuine OpenAI param — must survive
            "seed": 7,  # genuine OpenAI param — must survive
        },
    )
    kwargs = _to_openai_kwargs(req)
    for dropped in ("num_ctx", "num_gpu", "keep_alive", "top_k"):
        assert dropped not in kwargs, f"{dropped} leaked into the OpenAI call"
    assert kwargs["top_p"] == 0.9
    assert kwargs["seed"] == 7


def test_openai_extra_is_never_filtered() -> None:
    # Runtime ``extra`` is authoritative passthrough (e.g. base_url, OpenRouter
    # headers) — even an Ollama-looking key there is intentional and kept.
    req = CompletionRequest(
        model="m",
        messages=(Message(role="user", content="hi"),),
        extra={"num_ctx": 4096},
    )
    assert _to_openai_kwargs(req)["num_ctx"] == 4096


def test_google_json_schema_sets_response_schema() -> None:
    config = _to_google_config(_req(_JSON_SCHEMA_RF))
    assert config.response_mime_type == "application/json"
    assert config.response_schema == _OUTPUT_SCHEMA


def test_google_json_object_no_response_schema() -> None:
    config = _to_google_config(_req({"type": "json_object"}))
    assert config.response_mime_type == "application/json"
    assert config.response_schema is None


# ---- generic per-model options passthrough -------------------------------
#
# A model config's ``options`` block carries provider-native runtime params
# from the artifact YAML. Each provider folds them into its native call — for
# Ollama into the ``options`` object, for the others as top-level params —
# applied after the first-class fields so a same-named option overrides them.


def _opts_req(options: dict[str, Any], **kw: Any) -> CompletionRequest:
    return CompletionRequest(
        model="m",
        messages=(Message(role="user", content="hi"),),
        options=options,
        **kw,
    )


def test_ollama_options_fold_into_options_object() -> None:
    payload = _to_ollama_payload(
        _opts_req({"num_ctx": 8192, "repeat_penalty": 1.15}, temperature=0.0)
    )
    assert payload["options"]["num_ctx"] == 8192
    assert payload["options"]["repeat_penalty"] == 1.15
    # first-class temperature still present alongside the options
    assert payload["options"]["temperature"] == 0.0


def test_ollama_options_override_first_class() -> None:
    # An option with the same name as a first-class field wins.
    payload = _to_ollama_payload(_opts_req({"temperature": 0.9}, temperature=0.0))
    assert payload["options"]["temperature"] == 0.9


def test_ollama_no_options_omits_them() -> None:
    payload = _to_ollama_payload(
        CompletionRequest(model="m", messages=(Message(role="user", content="hi"),))
    )
    assert "options" not in payload


def test_openai_options_become_top_level_kwargs() -> None:
    kwargs = _to_openai_kwargs(_opts_req({"top_p": 0.9, "frequency_penalty": 0.2}))
    assert kwargs["top_p"] == 0.9
    assert kwargs["frequency_penalty"] == 0.2


def test_google_options_become_config_fields() -> None:
    config = _to_google_config(_opts_req({"top_p": 0.8, "top_k": 40}))
    assert config.top_p == 0.8
    assert config.top_k == 40
