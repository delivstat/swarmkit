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


def test_a_cors_preflight_is_not_asked_for_a_token() -> None:
    """A browser sends the preflight BEFORE the request that carries `Authorization`, and never
    with credentials. The auth middleware sits outside the CORS middleware and answered every
    preflight 401, so a portal on another origin could not make a single authenticated call —
    every one died as "Failed to fetch". The preflight passes through; the real request is still
    authenticated."""
    provider = APIKeyAuthProvider(keys=[{"key_ref": "secret", "client_id": "cp", "tier": "read"}])
    app = create_app(EXAMPLE_WS, auth_provider=provider, cors_origins=["http://portal.test"])
    with TestClient(app) as client:
        res = client.options(
            "/topologies",
            headers={
                "Origin": "http://portal.test",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert res.status_code == 200, res.text
        assert res.headers["access-control-allow-origin"] == "http://portal.test"
        # The request that follows the preflight is what carries the credential.
        assert (
            client.get("/topologies", headers={"Origin": "http://portal.test"}).status_code == 401
        )
        assert (
            client.get(
                "/topologies",
                headers={"Origin": "http://portal.test", "Authorization": "Bearer secret"},
            ).status_code
            == 200
        )
