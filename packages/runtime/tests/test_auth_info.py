"""`GET /auth-info` + provider `public_info()` — the unauthenticated auth-discovery endpoint a UI
reads to render the right login gate (design: details/workspace-ui.md § Auth)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from swarmkit_runtime.auth import APIKeyAuthProvider, JWTAuthProvider, NoneAuthProvider
from swarmkit_runtime.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


def test_none_provider_public_info() -> None:
    assert NoneAuthProvider().public_info() == {"mode": "none"}


def test_api_key_provider_public_info() -> None:
    provider = APIKeyAuthProvider(keys=[{"key_ref": "s", "client_id": "cp", "tier": "read"}])
    assert provider.public_info() == {"mode": "api_key"}


def test_jwt_provider_public_info_advertises_issuer_and_audience() -> None:
    provider = JWTAuthProvider(issuer="https://idp.example", audience="swarmkit")
    assert provider.public_info() == {
        "mode": "jwt",
        "oidc": {"issuer": "https://idp.example", "audience": "swarmkit"},
    }


def test_auth_info_endpoint_defaults_to_none() -> None:
    with TestClient(create_app(EXAMPLE_WS)) as client:
        res = client.get("/auth-info")
        assert res.status_code == 200
        assert res.json() == {"mode": "none"}


def test_auth_info_is_public_even_when_api_key_is_required() -> None:
    provider = APIKeyAuthProvider(keys=[{"key_ref": "secret", "client_id": "cp", "tier": "read"}])
    with TestClient(create_app(EXAMPLE_WS, auth_provider=provider)) as client:
        # /auth-info is reachable WITHOUT a token — a client reads it before logging in.
        res = client.get("/auth-info")
        assert res.status_code == 200
        assert res.json() == {"mode": "api_key"}
        # ...while a protected route without the token is still 401 (auth IS enforced elsewhere).
        assert client.get("/topologies").status_code == 401
