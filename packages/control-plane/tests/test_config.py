"""Tests for the read-only /config endpoint (Settings page) — flags + URLs, never secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._oidc import OidcVerifier


def _client(tmp_path: Path, **kw: Any) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    return TestClient(create_app(registry, verify=verify, **kw))


def test_config_open_mode_defaults(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/config").json()
    assert body["auth"]["operator_tokens"] is False
    assert body["auth"]["oidc"]["enabled"] is False
    assert body["cors_origins"] == []
    assert "version" in body


def test_config_reflects_configuration_without_secrets(tmp_path: Path) -> None:
    oidc = OidcVerifier(issuer="https://idp.example", audience="swarmkit-control-plane")
    client = _client(
        tmp_path,
        operator_tokens=["super-secret-token"],
        oidc=oidc,
        cors_origins=["http://localhost:3000"],
        observability={
            "jaeger_url": "http://localhost:16686",
            "grafana_url": "",
            "collector_endpoint": "",
        },
    )
    body = client.get("/config", headers={"Authorization": "Bearer super-secret-token"}).json()
    assert body["auth"]["operator_tokens"] is True  # flag only — never the value
    assert "super-secret-token" not in str(body)
    assert body["auth"]["oidc"] == {
        "enabled": True,
        "issuer": "https://idp.example",
        "audience": "swarmkit-control-plane",
    }
    assert body["cors_origins"] == ["http://localhost:3000"]
    assert body["observability"]["jaeger_url"] == "http://localhost:16686"


def test_config_read_is_operator_only_under_auth(tmp_path: Path) -> None:
    client = _client(tmp_path, operator_tokens=["op"])
    assert client.get("/config", headers={"Authorization": "Bearer op"}).status_code == 200
    assert client.get("/config").status_code == 401
