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
