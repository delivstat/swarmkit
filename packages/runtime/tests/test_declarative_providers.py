"""Declarative model providers (design/details/declarative-model-providers.md).

A provider is YAML over a wire-format family. These tests cover the test plan in the note, in its
order: parse and refuse; inheritance merges by key; the migration of the three aggregator
subclasses is behaviour-identical; registration by readiness; a workspace overrides bundled;
``${VAR:-default}`` resolves; and the bundled library is valid.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime.model_providers import (
    CompletionRequest,
    Message,
    ProviderRegistry,
    ProviderSpecError,
    ToolSpec,
    build_provider,
    load_provider_specs,
    resolve_chain,
)
from swarmkit_runtime.model_providers._declarative import (
    _BUNDLED_PROVIDERS_DIR,
    FAMILIES,
    ProviderSpec,
    parse_provider_spec,
)
from swarmkit_runtime.model_providers._family import CapabilityError
from swarmkit_runtime.model_providers._registry import provider_enforces_response_schema
from swarmkit_schema import validate


def _artifact(pid: str, extends: str, **spec: Any) -> dict[str, Any]:
    return {
        "apiVersion": "swarmkit/v1",
        "kind": "ModelProvider",
        "metadata": {"id": pid, "name": pid, "description": f"test provider {pid} for the suite"},
        "spec": {"extends": extends, **spec},
        "provenance": {"authored_by": "human", "version": "1.0.0"},
    }


def _spec(pid: str, extends: str, **spec: Any) -> ProviderSpec:
    return parse_provider_spec(_artifact(pid, extends, **spec))


def _write(directory: Path, pid: str, extends: str, **spec: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{pid}.yaml"
    path.write_text(yaml.safe_dump(_artifact(pid, extends, **spec)), encoding="utf-8")
    return path


# ---- parse and refuse --------------------------------------------------------------------------


def test_every_field_parses() -> None:
    spec = _spec(
        "full",
        "openai-compatible",
        base_url="https://x/v1",
        auth={"api_key_env": "X_KEY", "header": "api-key", "scheme": ""},
        models={"pattern": "^m-", "accept_any": False},
        capabilities={"tools": False},
        options={"lift_to_root": ["think"]},
        headers={"X-Title": "swarmkit"},
        extra_body={"usage": {"include": True}},
    )
    assert spec.base_url == "https://x/v1"
    assert spec.auth == {"api_key_env": "X_KEY", "header": "api-key", "scheme": ""}
    assert spec.models == {"pattern": "^m-", "accept_any": False}
    assert spec.capabilities == {"tools": False}
    assert spec.lift_to_root == ("think",)
    assert spec.headers == {"X-Title": "swarmkit"}
    assert spec.extra_body == {"usage": {"include": True}}


def test_requires_code_is_refused_with_the_file_named(tmp_path: Path) -> None:
    """The artifact is VALID — the schema accepts it — and unloadable. A YAML past the ceiling
    fails loudly, naming itself, rather than half-working."""
    path = _write(tmp_path / "providers", "vertex", "google", requires="code")
    with pytest.raises(ProviderSpecError) as exc:
        load_provider_specs(tmp_path)
    assert str(path) in str(exc.value)
    assert "requires: code" in str(exc.value)


def test_a_chain_that_never_reaches_a_family_is_refused_with_the_chain() -> None:
    specs = {"a": _spec("a", "b"), "b": _spec("b", "nowhere")}
    with pytest.raises(ProviderSpecError, match="never reaches a family: a -> b -> nowhere"):
        resolve_chain("a", specs)


def test_a_cycle_is_refused_with_the_chain() -> None:
    specs = {"a": _spec("a", "b"), "b": _spec("b", "a")}
    with pytest.raises(ProviderSpecError, match="cycle: a -> b -> a"):
        resolve_chain("a", specs)


def test_a_capability_the_family_lacks_is_refused() -> None:
    """Anthropic does not read ``response_format``; a YAML claiming it does is how a schema stops
    being pasted into the prompt for a provider that needed it there."""
    specs = {"c": _spec("c", "anthropic", capabilities={"structured_output": True})}
    with pytest.raises(ProviderSpecError, match=r"structured_output.*not implemented.*anthropic"):
        resolve_chain("c", specs)


def test_narrowing_a_capability_is_allowed() -> None:
    specs = {"c": _spec("c", "ollama", capabilities={"tools": False})}
    resolved = resolve_chain("c", specs)
    assert resolved.capabilities["tools"] is False
    assert resolved.capabilities["images"] is True  # untouched: the family's


def test_a_field_the_family_would_ignore_is_refused() -> None:
    """Refused, not dropped: ``extra_body`` on the Anthropic family reaches no wire, and a
    declared quirk that silently does nothing is the half-working provider this exists to stop."""
    specs = {"c": _spec("c", "anthropic", extra_body={"x": 1})}
    with pytest.raises(
        ProviderSpecError, match="extra_body would be ignored by family 'anthropic'"
    ):
        resolve_chain("c", specs)


def test_a_bad_model_pattern_is_refused() -> None:
    specs = {"c": _spec("c", "ollama", models={"pattern": "("})}
    with pytest.raises(ProviderSpecError, match="not a regex"):
        resolve_chain("c", specs)


def test_duplicate_ids_in_one_directory_are_refused(tmp_path: Path) -> None:
    d = tmp_path / "providers"
    _write(d, "one", "ollama")
    (d / "two.yaml").write_text(yaml.safe_dump(_artifact("one", "ollama")), encoding="utf-8")
    with pytest.raises(ProviderSpecError, match="already declared"):
        load_provider_specs(tmp_path)


# ---- inheritance -------------------------------------------------------------------------------


def test_inheritance_merges_by_key() -> None:
    """A child overriding ``auth.header`` keeps the parent's ``api_key_env``; headers union;
    the scalar ``base_url`` is replaced; the chain is recorded leaf to family."""
    specs = {
        "parent": _spec(
            "parent",
            "openai-compatible",
            base_url="https://parent/v1",
            auth={"api_key_env": "P_KEY"},
            headers={"A": "1"},
        ),
        "child": _spec(
            "child",
            "parent",
            base_url="https://child/v1",
            auth={"header": "api-key", "scheme": ""},
            headers={"B": "2"},
        ),
    }
    r = resolve_chain("child", specs)
    assert r.chain == ("child", "parent", "openai-compatible")
    assert r.base_url == "https://child/v1"
    assert r.auth.api_key_env == "P_KEY"
    assert r.auth.header == "api-key"
    assert r.auth.scheme == ""
    assert r.headers == {"A": "1", "B": "2"}


def test_a_provider_named_after_its_family_is_still_a_provider() -> None:
    """The bug this guards: ``anthropic`` the provider extends ``anthropic`` the family. A walk
    that checked the family table before the provider table returned the bare family — and the
    YAML's ``auth.api_key_env`` was never read, so nothing ever registered."""
    r = resolve_chain("anthropic", load_provider_specs())
    assert r.chain == ("anthropic", "anthropic")
    assert r.auth.api_key_env == "ANTHROPIC_API_KEY"


def test_extends_names_the_family_before_a_provider_of_the_same_name() -> None:
    """``rkllama extends ollama`` is the wire format, not the bundled ollama.yaml — otherwise a
    workspace override of ollama's base_url would silently move rkllama too."""
    r = resolve_chain("rkllama", load_provider_specs())
    assert r.chain == ("rkllama", "ollama")
    assert r.base_url == "http://localhost:8080"


def test_a_provider_that_says_nothing_is_its_family() -> None:
    """The family defaults are what the classes did before they were parameterised."""
    r = resolve_chain("o", {"o": _spec("o", "ollama")})
    assert r.base_url == "http://localhost:11434"
    assert r.accept_any_model is True
    assert r.lift_to_root == ("think", "keep_alive")
    r = resolve_chain("a", {"a": _spec("a", "anthropic")})
    assert r.model_pattern == "^claude-"
    assert r.accept_any_model is False


# ---- migration: behaviour-identical --------------------------------------------------------


@pytest.mark.parametrize(
    ("pid", "base_url", "env"),
    [
        ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
        ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
        ("together", "https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    ],
)
def test_the_aggregators_build_the_same_client_the_deleted_classes_did(
    pid: str, base_url: str, env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three subclasses were a base_url, an env var and ``supports() -> True``. The YAMLs
    are asserted to be exactly that, with the same provider id."""
    monkeypatch.setenv(env, "sk-test")
    provider = build_provider(resolve_chain(pid, load_provider_specs()))
    assert provider.provider_id == pid
    assert str(provider._client.base_url).rstrip("/") == base_url  # type: ignore[attr-defined]
    assert provider._client.api_key == "sk-test"  # type: ignore[attr-defined]
    assert provider.supports("anything/at-all")


def test_the_first_party_providers_keep_their_catalogues(monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(env, "k")
    specs = load_provider_specs()
    openai = build_provider(resolve_chain("openai", specs))
    assert openai.supports("gpt-4o") and not openai.supports("claude-3")
    anthropic = build_provider(resolve_chain("anthropic", specs))
    assert anthropic.supports("claude-sonnet-4-6") and not anthropic.supports("gpt-4o")
    google = build_provider(resolve_chain("google", specs))
    assert google.supports("gemini-2.5-flash") and not google.supports("gpt-4o")


def test_unparameterised_families_are_unchanged() -> None:
    """Constructing a family with no parameters is exactly the pre-YAML class."""
    from swarmkit_runtime.model_providers._ollama import OllamaModelProvider  # noqa: PLC0415
    from swarmkit_runtime.model_providers._openai import OpenAIModelProvider  # noqa: PLC0415

    o = OllamaModelProvider()
    assert o.provider_id == "ollama" and o.supports("anything")
    assert o._base_url == "http://localhost:11434"
    p = OpenAIModelProvider(api_key="k")
    assert p.provider_id == "openai"
    assert p.supports("gpt-4o") and not p.supports("llama")


# ---- registration by readiness ---------------------------------------------------------------


def _registered(monkeypatch: pytest.MonkeyPatch, root: Path | None = None) -> list[str]:
    from swarmkit_runtime._workspace_runtime import register_available_providers  # noqa: PLC0415

    registry = ProviderRegistry()
    register_available_providers(registry, root)
    return registry.provider_ids


def test_a_keyed_provider_registers_only_when_its_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert "groq" not in _registered(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert "groq" in _registered(monkeypatch)


def test_local_runtimes_register_regardless(monkeypatch: pytest.MonkeyPatch) -> None:
    """No auth means nothing to wait for. This is the old unconditional Ollama registration,
    made general: rkllama, llama-server and the rest register the same way."""
    for env in ("OPENAI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    ids = _registered(monkeypatch)
    for pid in ("ollama", "rkllama", "llama-server", "openvino-model-server", "mlx-lm", "lemonade"):
        assert pid in ids, pid
    assert "mock" in ids


def test_there_is_no_list_to_keep_in_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider the runtime has never heard of, added as a file, registers. No Python."""
    _write(tmp_path / "providers", "brand-new", "openai-compatible", base_url="http://x/v1")
    assert "brand-new" in _registered(monkeypatch, tmp_path)


# ---- workspace overrides bundled ------------------------------------------------------------


def test_a_workspace_provider_overrides_a_bundled_one(tmp_path: Path) -> None:
    _write(tmp_path / "providers", "ollama", "ollama", base_url="http://gpu-box:11434")
    r = resolve_chain("ollama", load_provider_specs(tmp_path))
    assert r.base_url == "http://gpu-box:11434"
    # And the bundled one is untouched when no workspace is given.
    assert resolve_chain("ollama", load_provider_specs()).base_url == "http://localhost:11434"


# ---- ${VAR:-default} -------------------------------------------------------------------------


def test_base_url_resolves_env_with_default(monkeypatch: pytest.MonkeyPatch) -> None:
    specs = {"r": _spec("r", "ollama", base_url="${RKLLAMA_HOST:-http://localhost:8080}")}
    monkeypatch.delenv("RKLLAMA_HOST", raising=False)
    assert resolve_chain("r", specs).base_url == "http://localhost:8080"
    monkeypatch.setenv("RKLLAMA_HOST", "http://rock:8080")
    assert resolve_chain("r", specs).base_url == "http://rock:8080"


# ---- the bundled library ---------------------------------------------------------------------


def test_the_bundled_library_is_valid_and_unique() -> None:
    paths = sorted(_BUNDLED_PROVIDERS_DIR.glob("*.yaml"))
    assert paths, "the bundled library is empty"
    ids: list[str] = []
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        validate("model-provider", raw)
        assert path.stem == raw["metadata"]["id"], f"{path.name}: file name is not its id"
        ids.append(raw["metadata"]["id"])
    assert len(ids) == len(set(ids))
    # Every bundled provider resolves, is one hop from its family, and names a real family.
    specs = load_provider_specs()
    for pid in ids:
        r = resolve_chain(pid, specs)
        assert len(r.chain) == 2, f"{pid}: bundled providers are one hop from a family"
        assert r.family in FAMILIES


def test_the_existing_eight_ids_are_all_present() -> None:
    """``provider: groq`` in a workspace works before and after."""
    ids = set(load_provider_specs())
    assert {"anthropic", "openai", "google", "ollama", "openrouter", "groq", "together"} <= ids


# ---- capabilities are honoured, not decorative ----------------------------------------------


@pytest.mark.asyncio
async def test_a_narrowed_capability_refuses_the_request_before_the_wire() -> None:
    """rkllama declares ``tools: false``. A request carrying tools is refused with the provider
    and the field named, instead of sent to a parser that half-understands it."""
    provider = build_provider(resolve_chain("rkllama", load_provider_specs()))
    req = CompletionRequest(
        model="qwen",
        messages=(Message(role="user", content="hi"),),
        tools=(ToolSpec(name="t", description="d", input_schema={}),),
    )
    with pytest.raises(CapabilityError, match=r"rkllama.*tools: false"):
        await provider.complete(req)


def test_structured_output_narrowing_reaches_the_compiler_lookup() -> None:
    """``provider_enforces_response_schema`` is what decides whether the schema is pasted into
    the prompt. A YAML that switches structured_output off must flip it, or the schema travels
    nowhere."""
    assert provider_enforces_response_schema("ollama") is True
    assert provider_enforces_response_schema("rkllama") is True  # not narrowed
    assert provider_enforces_response_schema("anthropic") is False


def test_structured_output_narrowed_in_a_workspace(tmp_path: Path) -> None:
    _write(
        tmp_path / "providers",
        "plain",
        "ollama",
        capabilities={"structured_output": False},
    )
    assert provider_enforces_response_schema("plain", tmp_path) is False
    provider = build_provider(resolve_chain("plain", load_provider_specs(tmp_path)))
    assert getattr(provider, "enforces_response_schema", None) is False


def test_the_ollama_lift_is_declared_not_hardcoded() -> None:
    """A YAML may change which keys are lifted to the payload root."""
    from swarmkit_runtime.model_providers._ollama import _to_ollama_payload  # noqa: PLC0415

    req = CompletionRequest(
        model="m",
        messages=(Message(role="user", content="hi"),),
        options={"think": False, "keep_alive": -1, "custom": 1},
    )
    default = _to_ollama_payload(req)
    assert default["think"] is False and default["keep_alive"] == -1
    assert default["options"] == {"custom": 1}
    narrowed = _to_ollama_payload(req, lift_to_root=("custom",))
    assert narrowed["custom"] == 1
    assert narrowed["options"] == {"think": False, "keep_alive": -1}


# ---- swarmkit providers ----------------------------------------------------------------------


def test_providers_list_shows_readiness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    _write(tmp_path / "providers", "llama-server", "openai-compatible", base_url="http://x/v1")
    result = CliRunner().invoke(app, ["providers", "list", str(tmp_path)])
    assert result.exit_code == 0, result.output
    lines = {ln.split()[0]: ln for ln in result.output.splitlines() if ln.strip()}
    assert "needs GROQ_API_KEY" in lines["groq"]
    assert "ready (OPENAI_API_KEY set)" in lines["openai"]
    assert "workspace" in lines["llama-server"] and "no auth" in lines["llama-server"]
    assert "bundled" in lines["rkllama"]


def test_providers_list_refuses_the_ceiling_loudly(tmp_path: Path) -> None:
    """The demo in the design note: a workspace YAML declaring requires: code stops the listing
    with the file and the reason, rather than being skipped."""
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    _write(tmp_path / "providers", "vertex", "google", requires="code")
    result = CliRunner().invoke(app, ["providers", "list", str(tmp_path)])
    assert result.exit_code == 1
    assert "vertex.yaml" in result.output and "requires: code" in result.output


def test_providers_show_prints_the_chain(tmp_path: Path) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    result = CliRunner().invoke(app, ["providers", "show", "rkllama", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "chain:        rkllama -> ollama" in result.output
    assert "tools=no" in result.output
    assert "lift_to_root: think, keep_alive" in result.output
