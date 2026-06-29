"""Tests for panel authentication — operator vs connector principals, open vs enforced."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app

_OP = "operator-secret-token"


def _client(tmp_path: Path, *, enforce: bool) -> TestClient:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    return TestClient(
        create_app(registry, verify=verify, operator_tokens=[_OP] if enforce else None)
    )


def _op(headers: dict[str, str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_OP}", **(headers or {})}


def _enroll_poll(client: TestClient, auth: dict[str, str]) -> str:
    resp = client.post(
        "/instances",
        json={"name": "edge", "endpoint": "n/a", "connection": "poll", "tier": "run"},
        headers=auth,
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["id"])


# --- open mode (no operator tokens) ---------------------------------------------


def test_open_mode_allows_everything(tmp_path: Path) -> None:
    client = _client(tmp_path, enforce=False)
    assert client.get("/instances").status_code == 200
    assert (
        client.post(
            "/instances", json={"name": "x", "endpoint": "n/a", "connection": "poll"}
        ).status_code
        == 200
    )


# --- enforced mode --------------------------------------------------------------


def test_health_is_exempt(tmp_path: Path) -> None:
    client = _client(tmp_path, enforce=True)
    assert client.get("/health").status_code == 200


def test_missing_or_bad_token_is_401(tmp_path: Path) -> None:
    client = _client(tmp_path, enforce=True)
    assert client.get("/instances").status_code == 401
    assert client.get("/instances", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_operator_token_has_full_access(tmp_path: Path) -> None:
    client = _client(tmp_path, enforce=True)
    iid = _enroll_poll(client, _op())
    assert client.get("/instances", headers=_op()).status_code == 200
    assert client.post(f"/instances/{iid}/mint-token", json={}, headers=_op()).status_code == 200


def test_connector_token_scoped_to_own_instance(tmp_path: Path) -> None:
    client = _client(tmp_path, enforce=True)
    iid = _enroll_poll(client, _op())
    other = _enroll_poll(client, _op())
    # Operator mints the instance's connector→panel token.
    token = client.post(f"/instances/{iid}/mint-token", json={}, headers=_op()).json()["token"]
    conn = {"Authorization": f"Bearer {token}"}

    # Allowed: poll its own instance.
    assert (
        client.post(f"/instances/{iid}/poll", json={"status": "ok"}, headers=conn).status_code
        == 200
    )
    # Allowed: report a result for its own instance (404 = handler ran, auth passed; not 403).
    r = client.post(
        f"/instances/{iid}/commands/missing/result", json={"status": "done"}, headers=conn
    )
    assert r.status_code == 404

    # Forbidden: list the fleet, enroll, mint, or touch another instance.
    assert client.get("/instances", headers=conn).status_code == 403
    assert client.post(f"/instances/{iid}/mint-token", json={}, headers=conn).status_code == 403
    assert (
        client.post(f"/instances/{other}/poll", json={"status": "ok"}, headers=conn).status_code
        == 403
    )


def test_rotated_token_supersedes_old(tmp_path: Path) -> None:
    client = _client(tmp_path, enforce=True)
    iid = _enroll_poll(client, _op())
    old = client.post(f"/instances/{iid}/mint-token", json={}, headers=_op()).json()["token"]
    new = client.post(f"/instances/{iid}/mint-token", json={}, headers=_op()).json()["token"]
    assert old != new
    # Only the current token authenticates (registry stores one hash per instance).
    assert (
        client.post(
            f"/instances/{iid}/poll",
            json={"status": "ok"},
            headers={"Authorization": f"Bearer {new}"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/instances/{iid}/poll",
            json={"status": "ok"},
            headers={"Authorization": f"Bearer {old}"},
        ).status_code
        == 401
    )
