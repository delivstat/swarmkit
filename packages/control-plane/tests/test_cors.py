"""Tests for panel CORS — localhost allowed by default, extra origins configurable."""

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


def test_localhost_origin_allowed_by_default(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for origin in ("http://localhost:3000", "http://127.0.0.1:5173", "https://localhost"):
        resp = client.get("/instances", headers={"Origin": origin})
        assert resp.headers.get("access-control-allow-origin") == origin


def test_preflight_allowed_for_localhost(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.options(
        "/instances",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_non_localhost_origin_blocked_without_config(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.get("/instances", headers={"Origin": "https://evil.example"})
    # No CORS header echoed → browser blocks the cross-origin read.
    assert "access-control-allow-origin" not in resp.headers


def test_configured_origin_allowed(tmp_path: Path) -> None:
    client = _client(tmp_path, cors_origins=["https://fleet.example.com"])
    resp = client.get("/instances", headers={"Origin": "https://fleet.example.com"})
    assert resp.headers.get("access-control-allow-origin") == "https://fleet.example.com"
    # An unconfigured non-localhost origin is still blocked.
    other = client.get("/instances", headers={"Origin": "https://other.example.com"})
    assert "access-control-allow-origin" not in other.headers
