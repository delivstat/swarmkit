"""The browser login flow, end to end against a stub provider.

The security assertions matter most here, because this is the half that is reachable from a
browser: no route may return a token, a replayed callback must find nothing, and a callback whose
`state` did not come from this session must be refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from swarmkit_runtime.oauth import KEY_ENV, PendingLogins, TokenStore
from swarmkit_runtime.server._routes_oauth import OAuthService, _register_oauth_routes

#: The genuine class, captured before any fixture patches the module attribute. A factory that
#: reads `httpx.AsyncClient` at call time would wrap the previous patch instead of replacing it,
#: and the second patch would silently inherit the first one's transport.
_REAL_ASYNC_CLIENT = httpx.AsyncClient

ENDPOINT = "https://mcp.example/mcp"
AUTH_META: dict[str, Any] = {
    "issuer": "https://auth.example",
    "authorization_endpoint": "https://auth.example/authorize",
    "token_endpoint": "https://auth.example/token",
    "registration_endpoint": "https://auth.example/register",
    "revocation_endpoint": "https://auth.example/revoke",
    "scopes_supported": ["read", "write"],
}

exchanges: list[dict[str, str]] = []


def provider(request: httpx.Request) -> httpx.Response:
    """A stub OAuth provider that behaves like a compliant MCP server."""
    url = str(request.url)
    if url == ENDPOINT:
        # The MCP authorization handshake starts with an unauthenticated probe.
        return httpx.Response(
            401,
            headers={
                "WWW-Authenticate": (
                    "Bearer resource_metadata="
                    '"https://mcp.example/.well-known/oauth-protected-resource"'
                )
            },
        )
    if url == AUTH_META["token_endpoint"]:
        exchanges.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200,
            json={
                "access_token": "access-xyz",
                "refresh_token": "refresh-xyz",
                "expires_in": 3600,
                "scope": "read write",
            },
        )
    static: dict[str, httpx.Response] = {
        "https://mcp.example/.well-known/oauth-protected-resource": httpx.Response(
            200, json={"authorization_servers": ["https://auth.example"]}
        ),
        "https://auth.example/.well-known/oauth-authorization-server": httpx.Response(
            200, json=AUTH_META
        ),
        str(AUTH_META["registration_endpoint"]): httpx.Response(
            201, json={"client_id": "registered-client"}
        ),
        str(AUTH_META["revocation_endpoint"]): httpx.Response(200),
    }
    return static.get(url, httpx.Response(404))


@pytest.fixture(autouse=True)
def _stub_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every outbound call in the flow at the stub."""
    exchanges.clear()

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(provider)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr("swarmkit_runtime.server._routes_oauth.httpx.AsyncClient", factory)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv(KEY_ENV, raising=False)
    app = FastAPI()
    store = TokenStore(tmp_path)
    _register_oauth_routes(app, OAuthService(store=store, pending=PendingLogins()))
    c = TestClient(app)
    c.store = store  # type: ignore[attr-defined]
    return c


def _login(client: TestClient) -> str:
    resp = client.post("/api/oauth/login", json={"credential_id": "linear", "endpoint": ENDPOINT})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["state"])


# ---- the happy path --------------------------------------------------------------------------


def test_login_discovers_registers_and_returns_an_authorization_url(client: TestClient) -> None:
    resp = client.post("/api/oauth/login", json={"credential_id": "linear", "endpoint": ENDPOINT})
    url = resp.json()["authorization_url"]
    assert url.startswith(AUTH_META["authorization_endpoint"])
    # Registered itself: most MCP providers have no developer portal to pre-register in.
    assert "client_id=registered-client" in url
    assert "code_challenge_method=S256" in url
    assert "resource=" in url


def test_callback_exchanges_and_stores(client: TestClient) -> None:
    state = _login(client)
    resp = client.get("/auth/mcp/callback", params={"state": state, "code": "the-code"})
    assert resp.status_code == 200
    assert "Connected linear" in resp.text
    # The code was redeemed with the verifier, which is what makes an intercepted code useless.
    assert exchanges[-1]["grant_type"] == "authorization_code"
    assert exchanges[-1]["code_verifier"]

    meta = client.store.metadata("linear", "local")  # type: ignore[attr-defined]
    assert meta is not None
    assert meta.has_refresh_token is True
    assert meta.scopes == ["read", "write"]


def test_the_listing_returns_metadata_and_never_a_token(client: TestClient) -> None:
    state = _login(client)
    client.get("/auth/mcp/callback", params={"state": state, "code": "c"})
    body = client.get("/api/oauth/credentials").text
    assert "access-xyz" not in body
    assert "refresh-xyz" not in body
    assert '"has_refresh_token":true' in body.replace(" ", "")


# ---- refusals --------------------------------------------------------------------------------


def test_a_callback_with_an_unknown_state_is_refused(client: TestClient) -> None:
    """The CSRF case — somebody else's authorization must not land in this session."""
    resp = client.get("/auth/mcp/callback", params={"state": "not-ours", "code": "c"})
    assert resp.status_code == 400
    assert "could not be matched" in resp.text
    assert not client.store.list_metadata()  # type: ignore[attr-defined]


def test_a_replayed_callback_finds_nothing(client: TestClient) -> None:
    state = _login(client)
    assert client.get("/auth/mcp/callback", params={"state": state, "code": "c"}).status_code == 200
    second = client.get("/auth/mcp/callback", params={"state": state, "code": "c"})
    assert second.status_code == 400


def test_a_provider_error_is_reported_not_swallowed(client: TestClient) -> None:
    state = _login(client)
    resp = client.get(
        "/auth/mcp/callback",
        params={"state": state, "error": "access_denied", "error_description": "user said no"},
    )
    assert resp.status_code == 400
    assert "user said no" in resp.text


def test_login_requires_both_fields(client: TestClient) -> None:
    assert client.post("/api/oauth/login", json={"endpoint": ENDPOINT}).status_code == 400


def test_a_server_without_oauth_is_reported_before_a_popup_opens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe exists so the page can say *this needs a client_id* before opening a window."""

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(lambda _: httpx.Response(404))
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr("swarmkit_runtime.server._routes_oauth.httpx.AsyncClient", factory)
    body = client.get("/auth/mcp/probe", params={"endpoint": ENDPOINT}).json()
    assert body["supported"] is False
    assert "may not support OAuth" in body["detail"]


def test_probe_reports_what_the_provider_supports(client: TestClient) -> None:
    body = client.get("/auth/mcp/probe", params={"endpoint": ENDPOINT}).json()
    assert body["supported"] is True
    assert body["supports_registration"] is True
    assert body["scopes_supported"] == ["read", "write"]


# ---- deletion --------------------------------------------------------------------------------


def test_delete_revokes_upstream_and_forgets_locally(client: TestClient) -> None:
    state = _login(client)
    client.get("/auth/mcp/callback", params={"state": state, "code": "c"})
    body = client.delete("/api/oauth/credentials/linear").json()
    assert body["deleted"] is True
    assert body["revoked_upstream"] is True
    assert not client.store.list_metadata()  # type: ignore[attr-defined]
