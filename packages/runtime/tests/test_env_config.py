"""Tests for workspace environment configuration (M6.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.resolver import _apply_env_interpolation
from swarmkit_runtime.resolver._env_config import (
    interpolate_dict,
    interpolate_value,
    load_env_config,
    load_env_config_typed,
)


class TestLoadEnvConfig:
    def test_loads_default_env_file(self, tmp_path: Path) -> None:
        (tmp_path / "workspace.env.yaml").write_text(
            "github:\n  token: my-token\nslack:\n  webhook_url: http://hooks.slack.com/xxx\n"
        )
        props = load_env_config(tmp_path)
        assert props["github.token"] == "my-token"
        assert props["slack.webhook_url"] == "http://hooks.slack.com/xxx"

    def test_loads_named_env_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "workspace.env.yaml").write_text("db:\n  url: dev-db\n")
        (tmp_path / "workspace.env.prod.yaml").write_text("db:\n  url: prod-db\n")

        monkeypatch.setenv("SWARMKIT_ENV", "prod")
        props = load_env_config(tmp_path)
        assert props["db.url"] == "prod-db"

    def test_falls_back_to_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "workspace.env.yaml").write_text("key: default-val\n")
        monkeypatch.setenv("SWARMKIT_ENV", "staging")
        props = load_env_config(tmp_path)
        assert props["key"] == "default-val"

    def test_no_env_file_returns_empty(self, tmp_path: Path) -> None:
        props = load_env_config(tmp_path)
        assert props == {}

    def test_resolves_env_vars_in_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_SECRET", "resolved-secret")
        (tmp_path / "workspace.env.yaml").write_text("api:\n  key: ${MY_SECRET}\n")
        props = load_env_config(tmp_path)
        assert props["api.key"] == "resolved-secret"

    def test_unresolved_env_var_kept_as_is(self, tmp_path: Path) -> None:
        (tmp_path / "workspace.env.yaml").write_text("api:\n  key: ${NONEXISTENT_VAR}\n")
        props = load_env_config(tmp_path)
        assert props["api.key"] == "${NONEXISTENT_VAR}"

    def test_nested_properties(self, tmp_path: Path) -> None:
        env_yaml = (
            "notifications:\n  slack:\n"
            "    webhook_url: http://example.com\n"
            "    channel: '#alerts'\n"
        )
        (tmp_path / "workspace.env.yaml").write_text(env_yaml)
        props = load_env_config(tmp_path)
        assert props["notifications.slack.webhook_url"] == "http://example.com"
        assert props["notifications.slack.channel"] == "#alerts"

    def test_invalid_yaml_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "workspace.env.yaml").write_text("{{invalid yaml")
        props = load_env_config(tmp_path)
        assert props == {}


class TestInterpolateValue:
    def test_string_substitution(self) -> None:
        props = {"github.token": "my-token"}
        result = interpolate_value("${github.token}", props)
        assert result == "my-token"

    def test_embedded_substitution(self) -> None:
        props = {"host": "example.com"}
        result = interpolate_value("https://${host}/api", props)
        assert result == "https://example.com/api"

    def test_no_substitution_needed(self) -> None:
        result = interpolate_value("plain-value", {})
        assert result == "plain-value"

    def test_unresolved_property_kept(self) -> None:
        result = interpolate_value("${unknown.prop}", {})
        assert result == "${unknown.prop}"

    def test_non_string_passthrough(self) -> None:
        assert interpolate_value(42, {}) == 42
        assert interpolate_value(True, {}) is True
        assert interpolate_value(None, {}) is None

    def test_dict_recursive(self) -> None:
        props = {"db.url": "postgres://prod"}
        data = {"connection": {"url": "${db.url}", "pool": 5}}
        result = interpolate_value(data, props)
        assert result["connection"]["url"] == "postgres://prod"
        assert result["connection"]["pool"] == 5

    def test_list_recursive(self) -> None:
        props = {"host": "example.com"}
        data = ["${host}", "other"]
        result = interpolate_value(data, props)
        assert result == ["example.com", "other"]


class TestInterpolateDict:
    def test_full_workspace_like_dict(self) -> None:
        props = {
            "github.token": "ghp_xxx",
            "slack.webhook": "http://hooks.slack.com/xxx",
        }
        workspace = {
            "mcp_servers": [
                {
                    "id": "github",
                    "env": {"GITHUB_TOKEN": "${github.token}"},
                }
            ],
            "notifications": [
                {
                    "provider": "slack",
                    "config": {"webhook_url": "${slack.webhook}"},
                }
            ],
        }
        result = interpolate_dict(workspace, props)
        assert result["mcp_servers"][0]["env"]["GITHUB_TOKEN"] == "ghp_xxx"
        assert result["notifications"][0]["config"]["webhook_url"] == "http://hooks.slack.com/xxx"

    def test_backward_compat_no_references(self) -> None:
        workspace = {
            "mcp_servers": [
                {
                    "id": "github",
                    "env": {"GITHUB_TOKEN": "inline-token"},
                }
            ],
        }
        result = interpolate_dict(workspace, {})
        assert result["mcp_servers"][0]["env"]["GITHUB_TOKEN"] == "inline-token"


class TestEnvVarAndDefaults:
    """Direct ${ENV_VAR} + ${VAR:-default} + escape resolution in artifacts
    (design/details/artifact-env-substitution.md)."""

    def test_env_var_resolves_without_property_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_MODEL", "anthropic/claude-sonnet-5")
        # empty property map — env var still resolves
        assert interpolate_value("${SDLC_MODEL}", {}) == "anthropic/claude-sonnet-5"

    def test_default_used_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SDLC_MODEL", raising=False)
        assert interpolate_value("${SDLC_MODEL:-fallback}", {}) == "fallback"

    def test_env_wins_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_MODEL", "real")
        assert interpolate_value("${SDLC_MODEL:-fallback}", {}) == "real"

    def test_property_map_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "model.x", "from-env"
        )  # (env keys can't really have dots, but check order)
        assert interpolate_value("${model.x}", {"model.x": "from-props"}) == "from-props"

    def test_escape_yields_literal(self) -> None:
        assert interpolate_value("$${NOT_A_VAR}", {}) == "${NOT_A_VAR}"

    def test_unresolved_no_default_left_literal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SDLC_NOPE", raising=False)
        assert interpolate_value("${SDLC_NOPE}", {}) == "${SDLC_NOPE}"

    def test_interpolates_nested_artifact_tree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_WRITING_PROVIDER", "openrouter")
        monkeypatch.delenv("SDLC_WRITING_MODEL", raising=False)
        art = {
            "defaults": {
                "model": {
                    "provider": "${SDLC_WRITING_PROVIDER:-openrouter}",
                    "name": "${SDLC_WRITING_MODEL:-deepseek/deepseek-v3}",
                }
            }
        }
        out = interpolate_dict(art, {})
        assert out["defaults"]["model"]["provider"] == "openrouter"
        assert out["defaults"]["model"]["name"] == "deepseek/deepseek-v3"


class TestApplyInterpolationWithoutEnvFile:
    """_apply_env_interpolation resolves ${ENV_VAR}/defaults for artifacts even with no
    workspace.env.yaml (the feature must not depend on an env file)."""

    def test_resolves_env_refs_with_no_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SDLC_REASONING_MODEL", "anthropic/claude-sonnet-5")

        class _Art:
            def __init__(self, raw: Any) -> None:
                self.raw = raw

        art = _Art(
            {"defaults": {"model": {"name": "${SDLC_REASONING_MODEL:-moonshotai/kimi-k2.5}"}}}
        )
        _apply_env_interpolation(tmp_path, [art])  # type: ignore[list-item]  # duck-typed .raw
        assert art.raw["defaults"]["model"]["name"] == "anthropic/claude-sonnet-5"


class TestWholeStringReferenceTypePreservation:
    """A whole-string reference carries the property's typed value, not its str() form.

    #879 — before this landed, a numeric option like `num_ctx: ${model.vision.num_ctx}`
    arrived at Ollama as the string "4096" and the provider 500'd. The fix: when the
    entire artifact value is one ${name} with nothing around it, the typed property
    wins over the stringified one.
    """

    def test_load_env_config_typed_preserves_scalar_types(self, tmp_path: Path) -> None:
        (tmp_path / "workspace.env.yaml").write_text(
            "model:\n"
            "  vision:\n"
            "    num_ctx: 4096\n"
            "    keep_alive: 300.5\n"
            "    stream: true\n"
            "    stop: null\n"
        )
        strings, typed = load_env_config_typed(tmp_path)
        # The string view keeps the str() coercion for System-page display.
        assert strings["model.vision.num_ctx"] == "4096"
        assert strings["model.vision.keep_alive"] == "300.5"
        assert strings["model.vision.stream"] == "True"
        # The typed view carries the original Python scalar.
        assert typed["model.vision.num_ctx"] == 4096
        assert isinstance(typed["model.vision.num_ctx"], int)
        assert typed["model.vision.keep_alive"] == 300.5
        assert isinstance(typed["model.vision.keep_alive"], float)
        assert typed["model.vision.stream"] is True
        assert typed["model.vision.stop"] is None

    def test_whole_reference_returns_int_not_str(self) -> None:
        strings = {"model.vision.num_ctx": "4096"}
        typed = {"model.vision.num_ctx": 4096}
        out = interpolate_value("${model.vision.num_ctx}", strings, typed)
        assert out == 4096
        assert isinstance(out, int)

    def test_whole_reference_returns_bool(self) -> None:
        typed = {"stream": True}
        out = interpolate_value("${stream}", {"stream": "True"}, typed)
        assert out is True

    def test_whole_reference_returns_list(self) -> None:
        typed = {"stops": ["<|end|>", "</s>"]}
        out = interpolate_value("${stops}", {"stops": "['<|end|>', '</s>']"}, typed)
        assert out == ["<|end|>", "</s>"]

    def test_mixed_reference_stays_string(self) -> None:
        """Concatenation only works on strings; a numeric embedded reference stays coerced."""
        strings = {"port": "4096"}
        typed = {"port": 4096}
        out = interpolate_value("port ${port} open", strings, typed)
        assert out == "port 4096 open"
        assert isinstance(out, str)

    def test_whole_reference_without_typed_dict_keeps_string(self) -> None:
        """Back-compat: callers that don't pass typed_properties get the string form."""
        strings = {"model.vision.num_ctx": "4096"}
        out = interpolate_value("${model.vision.num_ctx}", strings)
        assert out == "4096"

    def test_whole_reference_env_var_fallback_returns_env_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_INT", "42")
        out = interpolate_value("${MY_INT}", {}, {})
        # Env values are strings by nature; honestly surface that.
        assert out == "42"

    def test_whole_reference_with_default_returns_default(self) -> None:
        out = interpolate_value("${missing:-fallback}", {}, {})
        assert out == "fallback"

    def test_whole_reference_unresolved_leaves_literal(self) -> None:
        out = interpolate_value("${missing}", {}, {})
        assert out == "${missing}"

    def test_apply_interpolation_lands_typed_value_in_artifact(self, tmp_path: Path) -> None:
        """End-to-end: a whole-string reference inside an archetype's options dict lands
        as the original int, so a provider that validates `num_ctx: int` accepts it."""
        (tmp_path / "workspace.env.yaml").write_text("model:\n  vision:\n    num_ctx: 4096\n")

        class _Art:
            def __init__(self, raw: Any) -> None:
                self.raw = raw

        art = _Art(
            {
                "defaults": {
                    "model": {
                        "name": "qwen2.5vl",
                        "options": {"num_ctx": "${model.vision.num_ctx}"},
                    }
                }
            }
        )
        _apply_env_interpolation(tmp_path, [art])  # type: ignore[list-item]
        assert art.raw["defaults"]["model"]["options"]["num_ctx"] == 4096
        assert isinstance(art.raw["defaults"]["model"]["options"]["num_ctx"], int)

    def test_whole_reference_to_string_property_still_expands_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression for #1072: when a property is itself a string containing an
        ``${ENV:-default}``, a whole-string reference to it must get the string-expanded
        view, not the raw typed value. Returning the raw string broke harness configs
        like ``model: ${models.analyst}`` where ``models.analyst: ${MY_MODEL:-sonnet}``.
        """
        monkeypatch.delenv("MY_ANALYST_MODEL", raising=False)
        strings = {"models.analyst": "sonnet"}
        typed = {"models.analyst": "${MY_ANALYST_MODEL:-sonnet}"}
        out = interpolate_value("${models.analyst}", strings, typed)
        assert out == "sonnet"

        monkeypatch.setenv("MY_ANALYST_MODEL", "opus")
        strings = {"models.analyst": "opus"}  # as _resolve_env_vars would produce
        out = interpolate_value("${models.analyst}", strings, typed)
        assert out == "opus"
