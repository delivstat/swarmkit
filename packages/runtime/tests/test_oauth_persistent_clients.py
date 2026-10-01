"""Persistent OAuth-client registration — the store, exchange_code's client_secret path, and
the full `prepare_login` → callback → token-stored round-trip against a mock Google-style
provider.

See `design/details/oauth-persistent-clients.md`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from swarmkit_runtime.oauth import KEY_ENV, PendingLogins, TokenStore
from swarmkit_runtime.oauth._client_store import CLIENT_TYPES, ClientStore
from swarmkit_runtime.oauth._pkce import exchange_code
from swarmkit_runtime.server._routes_oauth import OAuthService, _register_oauth_routes

_REAL_ASYNC_CLIENT = httpx.AsyncClient


# ---- client store ---------------------------------------------------------------------------


@pytest.fixture
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())


def test_client_store_round_trips_a_secret(tmp_path: Path, _key: None) -> None:
    store = ClientStore(tmp_path)
    display = store.save(
        endpoint="https://gmail.googleapis.com/mcp/v1",
        client_id="123.apps.googleusercontent.com",
        client_secret="hunter2",
        client_type="desktop",
        display_name="Google (DOT appliance)",
        scopes=["gmail.readonly"],
    )
    assert display.client_type == "desktop"
    # Display never carries the secret.
    assert not hasattr(display, "client_secret")

    record = store.get("https://gmail.googleapis.com/mcp/v1")
    assert record is not None
    assert record.client_secret == "hunter2"
    assert record.client_id == "123.apps.googleusercontent.com"


def test_client_store_rejects_unknown_client_type(tmp_path: Path, _key: None) -> None:
    store = ClientStore(tmp_path)
    with pytest.raises(ValueError, match="client_type"):
        store.save(
            endpoint="https://x",
            client_id="c",
            client_secret=None,
            client_type="installed",
            display_name="x",
        )


def test_client_store_replaces_existing_row(tmp_path: Path, _key: None) -> None:
    store = ClientStore(tmp_path)
    store.save(
        endpoint="https://x",
        client_id="first",
        client_secret="s1",
        client_type="desktop",
        display_name="first",
    )
    store.save(
        endpoint="https://x",
        client_id="second",
        client_secret="s2",
        client_type="web",
        display_name="second",
    )
    r = store.get("https://x")
    assert r is not None
    assert r.client_id == "second"
    assert r.client_secret == "s2"
    assert len(store.list_display()) == 1


def test_client_store_delete(tmp_path: Path, _key: None) -> None:
    store = ClientStore(tmp_path)
    store.save(
        endpoint="https://x",
        client_id="c",
        client_secret=None,
        client_type="desktop",
        display_name="x",
    )
    assert store.delete("https://x") is True
    assert store.get("https://x") is None
    assert store.delete("https://x") is False  # idempotent


def test_client_store_secret_is_encrypted_on_disk(tmp_path: Path, _key: None) -> None:
    """A naive `grep` on the SQLite file must not find the plaintext secret."""
    store = ClientStore(tmp_path)
    store.save(
        endpoint="https://x",
        client_id="c",
        client_secret="hunter2-plaintext",
        client_type="desktop",
        display_name="x",
    )
    store.close()
    db_bytes = (tmp_path / ".swarmkit" / "state" / "oauth_clients.db").read_bytes()
    assert b"hunter2-plaintext" not in db_bytes


def test_client_type_literal_matches_store_constants() -> None:
    assert set(CLIENT_TYPES) == {"desktop", "web", "dcr"}


# ---- exchange_code / client_secret ----------------------------------------------------------


@pytest.mark.anyio
async def test_exchange_code_omits_client_secret_for_public_clients() -> None:
    sent: dict[str, Any] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        sent.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "a", "expires_in": 3600})

    async with _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(transport)) as client:
        await exchange_code(
            {"token_endpoint": "https://tok"},
            code="x",
            verifier="v",
            client_id="c",
            redirect_uri="r",
            client=client,
        )
    assert "client_secret" not in sent


@pytest.mark.anyio
async def test_exchange_code_sends_client_secret_when_passed() -> None:
    sent: dict[str, Any] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        sent.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "a", "expires_in": 3600})

    async with _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(transport)) as client:
        await exchange_code(
            {"token_endpoint": "https://tok"},
            code="x",
            verifier="v",
            client_id="c",
            client_secret="hunter2",
            redirect_uri="r",
            client=client,
        )
    assert sent["client_secret"] == "hunter2"


# ---- full login flow against a Google-style provider (no DCR) -------------------------------


ENDPOINT = "https://gmail.googleapis.com/mcp/v1"
AUTH_META: dict[str, Any] = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    # Deliberately NO registration_endpoint — Google does not support DCR.
    "scopes_supported": ["https://www.googleapis.com/auth/gmail.readonly"],
}

token_requests: list[dict[str, str]] = []


def _provider(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == ENDPOINT:
        return httpx.Response(
            401,
            headers={
                "WWW-Authenticate": (
                    "Bearer resource_metadata="
                    '"https://gmail.googleapis.com/.well-known/oauth-protected-resource"'
                )
            },
        )
    if url == AUTH_META["token_endpoint"]:
        body = dict(httpx.QueryParams(request.content.decode()))
        token_requests.append(body)
        # Google's Desktop app client refuses the exchange without client_secret.
        if "client_secret" not in body:
            return httpx.Response(400, json={"error": "invalid_client"})
        return httpx.Response(
            200,
            json={
                "access_token": "ya29.real-looking-google-token",
                "refresh_token": "1//refresh",
                "expires_in": 3600,
                "scope": " ".join(AUTH_META["scopes_supported"]),
            },
        )
    static = {
        "https://gmail.googleapis.com/.well-known/oauth-protected-resource": httpx.Response(
            200, json={"authorization_servers": ["https://accounts.google.com"]}
        ),
        "https://accounts.google.com/.well-known/oauth-authorization-server": httpx.Response(
            200, json=AUTH_META
        ),
    }
    return static.get(url, httpx.Response(404))


@pytest.fixture(autouse=True)
def _stub_google(monkeypatch: pytest.MonkeyPatch) -> None:
    token_requests.clear()

    def async_client_factory(**kw: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(_provider), **kw)

    monkeypatch.setattr(
        "swarmkit_runtime.server._routes_oauth.httpx.AsyncClient", async_client_factory
    )
    monkeypatch.setattr("swarmkit_runtime.oauth._pkce.httpx.AsyncClient", async_client_factory)


def _client_with_stores(tmp_path: Path) -> TestClient:
    app = FastAPI()
    _register_oauth_routes(
        app,
        OAuthService(
            store=TokenStore(tmp_path),
            pending=PendingLogins(),
            clients=ClientStore(tmp_path),
        ),
    )
    return TestClient(app)


def test_login_without_registered_client_errors_cleanly(tmp_path: Path, _key: None) -> None:
    tc = _client_with_stores(tmp_path)
    res = tc.post(
        "/api/oauth/login",
        json={"credential_id": "gmail", "endpoint": ENDPOINT},
    )
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "dynamic client registration" in detail
    assert "/api/oauth/clients" in detail


def test_persistent_client_drives_the_full_round_trip(tmp_path: Path, _key: None) -> None:
    tc = _client_with_stores(tmp_path)

    # 1. Operator registers the Google OAuth client once.
    res = tc.post(
        "/api/oauth/clients",
        json={
            "endpoint": ENDPOINT,
            "client_id": "123.apps.googleusercontent.com",
            "client_secret": "GOCSPX-redacted",
            "client_type": "desktop",
            "display_name": "Google (DOT dev)",
            "scopes": AUTH_META["scopes_supported"],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["client_type"] == "desktop"
    # No secret in the response.
    assert "client_secret" not in body

    # 2. GET /api/oauth/clients lists it, scrubbed.
    listing = tc.get("/api/oauth/clients").json()["clients"]
    assert len(listing) == 1
    assert "client_secret" not in listing[0]
    assert listing[0]["client_id"] == "123.apps.googleusercontent.com"

    # 3. Login succeeds — DCR is unavailable so the stored client fills in.
    res = tc.post(
        "/api/oauth/login",
        json={"credential_id": "gmail", "endpoint": ENDPOINT},
    )
    assert res.status_code == 200
    state = res.json()["state"]

    # 4. Callback.
    res = tc.get(f"/auth/mcp/callback?state={state}&code=auth-code-abc")
    assert res.status_code == 200
    assert "Connected gmail" in res.text

    # 5. The token-exchange request carried the client_secret.
    assert len(token_requests) == 1
    assert token_requests[0]["client_secret"] == "GOCSPX-redacted"
    assert token_requests[0]["client_id"] == "123.apps.googleusercontent.com"

    # 6. A token row now exists, and no route returns the secret.
    stored = tc.get("/api/oauth/credentials").json()["credentials"]
    assert len(stored) == 1
    assert "access_ciphertext" not in stored[0]
    assert "GOCSPX" not in res.text


def test_body_client_id_overrides_persistent_client(tmp_path: Path, _key: None) -> None:
    tc = _client_with_stores(tmp_path)
    tc.post(
        "/api/oauth/clients",
        json={
            "endpoint": ENDPOINT,
            "client_id": "stored-id",
            "client_secret": "stored-secret",
            "client_type": "desktop",
            "display_name": "stored",
        },
    )
    res = tc.post(
        "/api/oauth/login",
        json={
            "credential_id": "gmail",
            "endpoint": ENDPOINT,
            "client_id": "body-id",
        },
    )
    assert res.status_code == 200
    state = res.json()["state"]
    tc.get(f"/auth/mcp/callback?state={state}&code=x")
    # body wins over stored.
    assert token_requests[-1]["client_id"] == "body-id"
    # And since the body did not pass a secret, the exchange went without one — which the
    # stub provider rejects. Confirming the override was total, not partial.
    assert "client_secret" not in token_requests[-1]


def test_delete_oauth_client_requires_endpoint(tmp_path: Path, _key: None) -> None:
    tc = _client_with_stores(tmp_path)
    tc.post(
        "/api/oauth/clients",
        json={
            "endpoint": ENDPOINT,
            "client_id": "x",
            "client_secret": "y",
            "client_type": "desktop",
            "display_name": "x",
        },
    )
    assert tc.delete("/api/oauth/clients", params={"endpoint": ENDPOINT}).json() == {
        "deleted": True
    }
    assert tc.get("/api/oauth/clients").json() == {"clients": []}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
