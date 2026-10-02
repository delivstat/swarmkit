"""Demo for the persistent OAuth-client feature.

Boots a FastAPI app with the OAuth routes + a mock Google-style provider wired via httpx's
MockTransport. Walks the operator path (register a client) and the user path (login →
callback → stored token). Prints a short transcript suitable for the PR body.

Run with:
    uv run python examples/oauth-persistent-client/demo.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from swarmkit_runtime.oauth import KEY_ENV, PendingLogins, TokenStore, _pkce
from swarmkit_runtime.oauth._client_store import ClientStore
from swarmkit_runtime.server import _routes_oauth
from swarmkit_runtime.server._routes_oauth import OAuthService, _register_oauth_routes

ENDPOINT = "https://gmail.googleapis.com/mcp/v1"
AUTH_META: dict[str, Any] = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "scopes_supported": ["https://www.googleapis.com/auth/gmail.readonly"],
}


def mock_google(request: httpx.Request) -> httpx.Response:
    """Mimics a provider that does not support dynamic client registration and demands
    client_secret at the token-exchange step — the Google Desktop/Web shape."""
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
        if "client_secret" not in body:
            return httpx.Response(400, json={"error": "invalid_client"})
        return httpx.Response(
            200,
            json={
                "access_token": "ya29.sample",
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


def build_app(workspace_path: Path) -> FastAPI:
    app = FastAPI()
    _register_oauth_routes(
        app,
        OAuthService(
            store=TokenStore(workspace_path),
            pending=PendingLogins(),
            clients=ClientStore(workspace_path),
        ),
    )
    return app


def main() -> int:
    os.environ.setdefault(KEY_ENV, Fernet.generate_key().decode())

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        app = build_app(workspace)

        # Wire the mock provider into the server's httpx clients.
        real_async_client = httpx.AsyncClient

        def factory(**kw: Any) -> httpx.AsyncClient:
            return real_async_client(transport=httpx.MockTransport(mock_google), **kw)

        _routes_oauth.httpx.AsyncClient = factory  # type: ignore[misc]
        _pkce.httpx.AsyncClient = factory  # type: ignore[misc]

        tc = TestClient(app)

        print("─── 1. Login without a registered client ──────────────────────────")
        res = tc.post(
            "/api/oauth/login",
            json={"credential_id": "gmail", "endpoint": ENDPOINT},
        )
        print(f"  {res.status_code} {res.json()}")

        print("\n─── 2. Operator registers the OAuth client once ───────────────────")
        res = tc.post(
            "/api/oauth/clients",
            json={
                "endpoint": ENDPOINT,
                "client_id": "123.apps.googleusercontent.com",
                "client_secret": "GOCSPX-redacted-secret",
                "client_type": "desktop",
                "display_name": "Google (DOT appliance)",
                "scopes": AUTH_META["scopes_supported"],
            },
        )
        print(f"  {res.status_code} {json.dumps(res.json(), indent=2)}")

        print("\n─── 3. GET /api/oauth/clients — no secret in listing ──────────────")
        print(f"  {json.dumps(tc.get('/api/oauth/clients').json(), indent=2)}")

        print("\n─── 4. Login succeeds — stored client fills in ────────────────────")
        res = tc.post(
            "/api/oauth/login",
            json={"credential_id": "gmail", "endpoint": ENDPOINT},
        )
        state = res.json()["state"]
        print(f"  {res.status_code} state={state[:8]}…")

        print("\n─── 5. Callback → token exchange → encrypted store ────────────────")
        res = tc.get(f"/auth/mcp/callback?state={state}&code=auth-code-abc")
        ok = "Connected gmail" in res.text
        print(f"  {res.status_code} connected={ok}")

        print("\n─── 6. Stored tokens — bytes never cross the HTTP boundary ────────")
        creds = tc.get("/api/oauth/credentials").json()
        print(f"  {json.dumps(creds, indent=2)}")

        print("\n─── 7. On-disk DB contains the client_secret only as ciphertext ───")
        db = (workspace / ".swarmkit" / "state" / "oauth_clients_v2.db").read_bytes()
        plaintext_present = b"GOCSPX-redacted-secret" in db
        print(f"  plaintext present in oauth_clients.db? {plaintext_present}")

        if plaintext_present:
            return 1
        if not ok:
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
