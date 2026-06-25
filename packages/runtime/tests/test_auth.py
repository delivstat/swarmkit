"""Tests for the AuthProvider abstraction (PR 2 of serve-and-auth).

Covers NoneAuthProvider, APIKeyAuthProvider, registry, and server
integration (auth middleware).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.auth import (
    APIKeyAuthProvider,
    AuthError,
    AuthRequest,
    NoneAuthProvider,
    default_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


# ---- helpers ---------------------------------------------------------------


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    path: str = "/run/hello",
    method: str = "POST",
) -> AuthRequest:
    return AuthRequest(
        headers=headers or {},
        path=path,
        method=method,
    )


# ---- NoneAuthProvider ------------------------------------------------------


@pytest.mark.asyncio()
async def test_none_provider_always_authenticates() -> None:
    provider = NoneAuthProvider()
    identity = await provider.authenticate(_make_request())
    assert identity.client_id == "anonymous"
    assert identity.provider == "none"
    assert "*" in identity.scopes


@pytest.mark.asyncio()
async def test_none_provider_always_authorizes() -> None:
    provider = NoneAuthProvider()
    identity = await provider.authenticate(_make_request())
    assert await provider.authorize(identity, "topologies", "run") is True
    assert await provider.authorize(identity, "anything", "whatever") is True


# ---- APIKeyAuthProvider ----------------------------------------------------


def _api_key_provider(
    *,
    key: str = "test-secret-key",
    client_id: str = "test-client",
    scopes: list[str] | None = None,
) -> APIKeyAuthProvider:
    return APIKeyAuthProvider(
        keys=[
            {
                "key_ref": key,
                "client_id": client_id,
                "client_name": "Test Client",
                "scopes": scopes or ["topologies:run", "jobs:read"],
            }
        ]
    )


@pytest.mark.asyncio()
async def test_api_key_valid_key() -> None:
    provider = _api_key_provider()
    identity = await provider.authenticate(
        _make_request(headers={"authorization": "Bearer test-secret-key"})
    )
    assert identity.client_id == "test-client"
    assert identity.client_name == "Test Client"
    assert identity.provider == "api_key"


@pytest.mark.asyncio()
async def test_api_key_invalid_key() -> None:
    provider = _api_key_provider()
    with pytest.raises(AuthError) as exc_info:
        await provider.authenticate(_make_request(headers={"authorization": "Bearer wrong-key"}))
    assert exc_info.value.status_code == 401
    assert "Missing or invalid API key" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_api_key_missing_header() -> None:
    provider = _api_key_provider()
    with pytest.raises(AuthError) as exc_info:
        await provider.authenticate(_make_request())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio()
async def test_api_key_scopes() -> None:
    provider = _api_key_provider(scopes=["topologies:run", "jobs:read"])
    identity = await provider.authenticate(
        _make_request(headers={"authorization": "Bearer test-secret-key"})
    )
    assert await provider.authorize(identity, "topologies", "run") is True
    assert await provider.authorize(identity, "jobs", "read") is True
    assert await provider.authorize(identity, "admin", "delete") is False


@pytest.mark.asyncio()
async def test_api_key_wildcard_scope() -> None:
    provider = _api_key_provider(scopes=["*"])
    identity = await provider.authenticate(
        _make_request(headers={"authorization": "Bearer test-secret-key"})
    )
    assert await provider.authorize(identity, "anything", "at_all") is True


@pytest.mark.asyncio()
async def test_api_key_env_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_SWARMKIT_KEY", "env-resolved-secret")
    provider = APIKeyAuthProvider(
        keys=[
            {
                "key_ref": "env:TEST_SWARMKIT_KEY",
                "client_id": "env-client",
                "client_name": "Env Client",
                "scopes": ["*"],
            }
        ]
    )
    identity = await provider.authenticate(
        _make_request(headers={"authorization": "Bearer env-resolved-secret"})
    )
    assert identity.client_id == "env-client"


# ---- registry --------------------------------------------------------------


def test_registry_has_builtins() -> None:
    assert "none" in default_registry.names
    assert "api_key" in default_registry.names


def test_registry_none_factory() -> None:
    factory = default_registry.get("none")
    assert factory is not None
    provider = factory()
    assert isinstance(provider, NoneAuthProvider)


def test_registry_api_key_factory() -> None:
    factory = default_registry.get("api_key")
    assert factory is not None
    provider = factory(keys=[{"key_ref": "x", "client_id": "c", "scopes": []}])
    assert isinstance(provider, APIKeyAuthProvider)


# ---- server integration ---------------------------------------------------


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


def _make_server_client(
    auth_provider: NoneAuthProvider | APIKeyAuthProvider | None = None,
) -> TestClient:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(EXAMPLE_WS, auth_provider=auth_provider)
    return TestClient(app)


def test_server_no_auth() -> None:
    """Server with NoneAuthProvider accepts all requests."""
    client = _make_server_client(NoneAuthProvider())
    with client:
        resp = client.get("/topologies")
    assert resp.status_code == 200


def test_server_api_key_rejects() -> None:
    """Server with APIKeyAuthProvider rejects unauthenticated requests."""
    provider = _api_key_provider()
    client = _make_server_client(provider)
    with client:
        resp = client.get("/topologies")
    assert resp.status_code == 401
    assert "error" in resp.json()


def test_server_api_key_accepts() -> None:
    """Server with APIKeyAuthProvider accepts a valid bearer token carrying serve:read."""
    # GET routes require the serve:read transport scope (per-route enforcement).
    provider = _api_key_provider(scopes=["serve:read"])
    client = _make_server_client(provider)
    with client:
        resp = client.get(
            "/topologies",
            headers={"Authorization": "Bearer test-secret-key"},
        )
    assert resp.status_code == 200


def test_health_skips_auth() -> None:
    """/health endpoint bypasses auth even with APIKeyAuthProvider."""
    provider = _api_key_provider()
    client = _make_server_client(provider)
    with client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_server_insufficient_scope_403() -> None:
    """Authenticated but under-scoped token is 403'd by per-route enforcement."""
    # serve:read token may GET, but an admin route (POST /api/reload) must 403.
    provider = _api_key_provider(scopes=["serve:read"])
    client = _make_server_client(provider)
    hdr = {"Authorization": "Bearer test-secret-key"}
    with client:
        assert client.get("/topologies", headers=hdr).status_code == 200
        assert client.post("/api/reload", headers=hdr).status_code == 403


def test_server_records_access_audit() -> None:
    """Authenticated mutating calls are recorded with the acting client_id."""
    provider = _api_key_provider(scopes=["serve:read"])  # read-only → admin call 403s
    client = _make_server_client(provider)
    hdr = {"Authorization": "Bearer test-secret-key"}
    with client:
        client.get("/topologies", headers=hdr)  # read — not audited
        client.post("/api/reload", headers=hdr)  # admin — audited (as a 403)
        store = client.app.state.store  # type: ignore[attr-defined]
        rows = store.list_access(limit=10)
    # the mutating call is recorded with the client id; the read GET is not
    paths = {(r["path"], r["status"]) for r in rows}
    assert ("/api/reload", 403) in paths
    assert all(r["client_id"] == "test-client" for r in rows)
    assert "/topologies" not in {r["path"] for r in rows}
