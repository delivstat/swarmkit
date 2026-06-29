"""Tests for the /observability config endpoint (dashboard links for the fleet UI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app

_OP = "operator-secret"


def _client(
    tmp_path: Path, *, observability: dict[str, str] | None = None, enforce: bool = False
) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    return TestClient(
        create_app(
            registry,
            verify=verify,
            observability=observability,
            operator_tokens=[_OP] if enforce else None,
        )
    )


def test_empty_when_unconfigured(tmp_path: Path) -> None:
    resp = _client(tmp_path).get("/observability")
    assert resp.status_code == 200
    assert resp.json() == {"collector_endpoint": "", "jaeger_url": "", "grafana_url": ""}


def test_returns_configured_urls(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        observability={
            "collector_endpoint": "http://collector:4318/v1/traces",
            "jaeger_url": "http://localhost:16686",
            "grafana_url": "http://localhost:3001",
        },
    )
    body = client.get("/observability").json()
    assert body["jaeger_url"] == "http://localhost:16686"
    assert body["grafana_url"] == "http://localhost:3001"
    assert body["collector_endpoint"].endswith("/v1/traces")


def test_read_is_operator_only_under_auth(tmp_path: Path) -> None:
    client = _client(tmp_path, observability={"jaeger_url": "http://j"}, enforce=True)
    # Operator reads it; an anonymous caller is unauthorized.
    assert (
        client.get("/observability", headers={"Authorization": f"Bearer {_OP}"}).status_code == 200
    )
    assert client.get("/observability").status_code == 401
