"""Tests for the control-plane API (enrollment, registry endpoints, heartbeat)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._connector import ConnectorError, resolve_token

_FAKE_CAPS = {
    "serve_version": "1.11.0",
    "schema_version": "1.6.0",
    "topologies": ["hello"],
    "model_providers": ["mock"],
}


def _client(tmp_path: Path, verify: Any = None) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")
    if verify is None:

        async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
            return _FAKE_CAPS

    return TestClient(create_app(registry, verify=verify))


def test_enroll_direct_verifies_and_stores(tmp_path: Path) -> None:
    client = _client(tmp_path)
    resp = client.post(
        "/instances",
        json={"name": "sterling", "endpoint": "http://sterling:8000", "token_ref": "env:T"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["health"] == "healthy"
    assert body["schema_version"] == "1.6.0"
    assert body["capabilities"]["topologies"] == ["hello"]
    assert "token_ref" not in body  # secret reference never returned
    # persisted + listable
    assert client.get("/instances").json()[0]["name"] == "sterling"


def test_enroll_direct_verification_failure_502(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str) -> dict[str, Any]:
        raise ConnectorError("unreachable")

    client = _client(tmp_path, verify=boom)
    # A token_ref is supplied, so enrollment pull-verifies (and fails). Without one, a direct
    # instance enrolls unverified for the mint-then-verify flow (see test_tokens.py).
    resp = client.post(
        "/instances", json={"name": "x", "endpoint": "http://x", "token_ref": "env:T"}
    )
    assert resp.status_code == 502
    assert client.get("/instances").json() == []  # not stored


def test_enroll_poll_skips_verify(tmp_path: Path) -> None:
    async def must_not_call(endpoint: str, token_ref: str) -> dict[str, Any]:
        raise AssertionError("poll enrollment must not pull-verify")

    client = _client(tmp_path, verify=must_not_call)
    resp = client.post(
        "/instances",
        json={"name": "minder", "endpoint": "http://nat", "connection": "poll"},
    )
    assert resp.status_code == 200
    assert resp.json()["health"] == "unknown"


def test_get_and_delete_and_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = client.post("/instances", json={"name": "a", "endpoint": "http://a"}).json()["id"]
    assert client.get(f"/instances/{iid}").json()["id"] == iid
    assert client.get("/instances/nope").status_code == 404
    assert client.delete(f"/instances/{iid}").json()["deleted"] == iid
    assert client.get(f"/instances/{iid}").status_code == 404


def test_heartbeat_updates_health(tmp_path: Path) -> None:
    client = _client(tmp_path)
    iid = client.post(
        "/instances", json={"name": "m", "endpoint": "http://nat", "connection": "poll"}
    ).json()["id"]
    assert client.get(f"/instances/{iid}").json()["health"] == "unknown"
    hb = client.post(
        f"/instances/{iid}/heartbeat",
        json={"status": "ok", "schema_version": "1.6.0"},
    )
    assert hb.status_code == 200
    got = client.get(f"/instances/{iid}").json()
    assert got["health"] == "healthy"
    assert got["schema_version"] == "1.6.0"
    assert got["last_seen"] is not None


def test_heartbeat_unknown_instance_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/instances/nope/heartbeat", json={}).status_code == 404


def test_connector_resolve_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CP_TOK", "abc")
    assert resolve_token("env:CP_TOK") == "abc"
    assert resolve_token("literal") == "literal"
    assert resolve_token("") is None
