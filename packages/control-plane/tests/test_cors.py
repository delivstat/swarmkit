"""Tests for panel CORS — entirely config-driven, no hardcoded origins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app


def _client(tmp_path: Path, cors_origins: list[str] | None = None) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    return TestClient(create_app(registry, verify=verify, cors_origins=cors_origins))


def test_no_origin_allowed_without_config(tmp_path: Path) -> None:
    client = _client(tmp_path)
    # Nothing is allowed by default — not even localhost (no hardcoded origins).
    for origin in ("http://localhost:3000", "http://127.0.0.1:5173", "https://evil.example"):
        resp = client.get("/instances", headers={"Origin": origin})
        assert "access-control-allow-origin" not in resp.headers


def test_configured_origin_allowed(tmp_path: Path) -> None:
    client = _client(tmp_path, cors_origins=["http://localhost:3000"])
    resp = client.get("/instances", headers={"Origin": "http://localhost:3000"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_preflight_allowed_for_configured_origin(tmp_path: Path) -> None:
    client = _client(tmp_path, cors_origins=["https://fleet.example.com"])
    resp = client.options(
        "/instances",
        headers={
            "Origin": "https://fleet.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://fleet.example.com"


def test_unconfigured_origin_blocked(tmp_path: Path) -> None:
    client = _client(tmp_path, cors_origins=["https://fleet.example.com"])
    resp = client.get("/instances", headers={"Origin": "https://other.example.com"})
    assert "access-control-allow-origin" not in resp.headers
