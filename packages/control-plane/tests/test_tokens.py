"""Tests for panel-side token minting + Mode A re-verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._tokens import fingerprint, mint_token

_CAPS = {"serve_version": "1.12.0", "schema_version": "1.6.0", "topologies": ["hello"]}


def _client(tmp_path: Path, verify: Any = None) -> tuple[TestClient, SqliteRegistry]:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")
    if verify is None:

        async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
            return _CAPS

    return TestClient(create_app(registry, verify=verify)), registry


def _enroll(client: TestClient, **kw: Any) -> str:
    body = {"name": "edge", "endpoint": "http://edge:8000", **kw}
    return str(client.post("/instances", json=body).json()["id"])


# --- pure helper ----------------------------------------------------------------


def test_mint_token_is_unique_and_tier_bound() -> None:
    a = mint_token("abc123", tier="run")
    b = mint_token("abc123", tier="run")
    assert a.token != b.token  # fresh secret each time
    assert a.client_id == "control-plane-abc123"
    assert a.key_ref == "env:SWARMKIT_SERVE_TOKEN_ABC123"
    assert a.fingerprint == fingerprint(a.token)
    assert "tier: run" in a.server_auth_snippet()
    assert "provider: api_key" in a.server_auth_snippet()


def test_mint_token_rejects_bad_tier() -> None:
    with pytest.raises(ValueError, match="invalid tier"):
        mint_token("abc123", tier="root")


# --- endpoint -------------------------------------------------------------------


def test_mint_endpoint_returns_secret_once_but_stores_only_metadata(tmp_path: Path) -> None:
    client, registry = _client(tmp_path)
    iid = _enroll(client, connection="poll", tier="run")

    resp = client.post(f"/instances/{iid}/mint-token", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]  # secret shown once
    assert body["tier"] == "run"  # defaults to the instance's granted tier
    assert body["key_ref"] == f"env:SWARMKIT_SERVE_TOKEN_{iid.upper()}"
    assert "server:" in body["server_auth_snippet"]

    # The registry persists the reference + fingerprint + metadata, never the secret.
    stored = registry.get(iid)
    assert stored is not None
    assert stored.token_fingerprint == body["fingerprint"]
    assert stored.token_ref == body["key_ref"]
    assert stored.token_minted_at is not None
    assert body["token"] not in stored.token_ref

    # public_dict never exposes a secret, only the fingerprint.
    pub = client.get(f"/instances/{iid}").json()
    assert pub["token_fingerprint"] == body["fingerprint"]
    assert "token" not in pub


def test_mint_can_override_tier(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    iid = _enroll(client, connection="poll", tier="read")
    body = client.post(f"/instances/{iid}/mint-token", json={"tier": "admin"}).json()
    assert body["tier"] == "admin"
    assert client.get(f"/instances/{iid}").json()["tier"] == "admin"


def test_mint_unknown_instance_404(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert client.post("/instances/nope/mint-token", json={}).status_code == 404


def test_enroll_direct_without_token_is_unverified_then_verify(tmp_path: Path) -> None:
    calls: list[str] = []

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:
        calls.append(token_ref)
        return _CAPS

    client, _ = _client(tmp_path, verify=verify)
    # No token_ref → enroll unverified, no pull attempted.
    iid = _enroll(client, connection="direct")
    assert calls == []
    assert client.get(f"/instances/{iid}").json()["health"] == "unknown"

    # Mint installs a token_ref; verify then pulls capabilities and marks healthy.
    client.post(f"/instances/{iid}/mint-token", json={})
    resp = client.post(f"/instances/{iid}/verify")
    assert resp.status_code == 200
    assert resp.json()["health"] == "healthy"
    assert resp.json()["schema_version"] == "1.6.0"
    assert len(calls) == 1


def test_verify_rejects_poll_mode(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    iid = _enroll(client, connection="poll")
    assert client.post(f"/instances/{iid}/verify").status_code == 409
