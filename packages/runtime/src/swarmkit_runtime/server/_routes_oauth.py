"""The browser login flow for remote MCP servers.

`design/details/mcp-oauth.md` — the portal flow, steps 2 to 5. The person clicks Connect, is sent
to the provider, comes back with a code, and the runtime exchanges it for tokens it stores
encrypted and per-owner.

**What this deliberately does not expose.** No route here returns a token. `GET /api/oauth/…`
answers with metadata — provider, owner, expiry, scopes, whether a refresh token exists — because
a token readable over HTTP is a token exfiltratable by anything that can read as its owner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from swarmkit_runtime.oauth import (
    PendingLogin,
    PendingLogins,
    TokenStore,
    authorization_url,
    discover_metadata,
    exchange_code,
    generate_verifier,
    register_client,
)

logger = logging.getLogger("swarmkit.oauth")

CALLBACK_PATH = "/auth/mcp/callback"


@dataclass
class OAuthService:
    """Login state for one serve process."""

    store: TokenStore
    pending: PendingLogins


def _owner_of(request: Request) -> str:
    """Whose token this will be.

    Unauthenticated serve yields the none-provider's default identity, which is the
    single-operator case. The owner is recorded either way, so turning auth on later does not
    silently merge one person's grants into a shared pool — the rows stay distinguishable, and a
    token obtained before auth was enabled keeps the owner it was obtained under.
    """
    identity = getattr(request.state, "identity", None)
    return str(getattr(identity, "client_id", "") or "local")


def _close_window(message: str, *, ok: bool) -> HTMLResponse:
    """The callback lands in a popup. Say what happened, then close.

    A bare JSON body here would leave the person staring at `{"saved": true}` in a window they have
    to dismiss themselves, unsure whether the page behind them knows.
    """
    colour = "#16a34a" if ok else "#dc2626"
    return HTMLResponse(
        f"""<!doctype html><meta charset=utf-8>
<title>SwarmKit</title>
<body style="font:14px system-ui;padding:2rem;color:{colour}">
<p>{message}</p>
<p style="color:#666">You can close this window.</p>
<script>
  if (window.opener) {{ window.opener.postMessage({{swarmkitOAuth:{str(ok).lower()}}}, "*"); }}
  setTimeout(() => window.close(), {1200 if ok else 6000});
</script>
</body>""",
        status_code=200 if ok else 400,
    )


async def prepare_login(
    body: dict[str, Any], redirect_uri: str, owner: str
) -> tuple[PendingLogin, str]:
    """Discover, register if needed, and build the authorization URL.

    Separate from the route so the discovery-and-registration path is testable without an app,
    which is where the fiddly parts are.
    """
    credential_id = str(body.get("credential_id", "")).strip()
    endpoint = str(body.get("endpoint", "")).strip()
    if not credential_id or not endpoint:
        raise HTTPException(400, "credential_id and endpoint are required")

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            metadata = await discover_metadata(endpoint, client=client)
        except LookupError as exc:
            raise HTTPException(400, str(exc)) from exc

        client_id = str(body.get("client_id", "")).strip()
        if not client_id:
            try:
                client_id = await register_client(
                    metadata, redirect_uri=redirect_uri, client=client
                )
            except PermissionError as exc:
                raise HTTPException(400, str(exc)) from exc
        if not client_id:
            raise HTTPException(
                400,
                "This provider does not support dynamic client registration. Register SwarmKit "
                "with it and pass the client_id.",
            )

    verifier = generate_verifier()
    login = PendingLogin(
        state=generate_verifier()[:32],
        verifier=verifier,
        credential_id=credential_id,
        endpoint=endpoint,
        owner=owner,
        redirect_uri=redirect_uri,
        metadata={**metadata, "client_id": client_id},
    )
    scopes = body.get("scopes") or metadata.get("scopes_supported") or []
    url = authorization_url(
        metadata,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=login.state,
        verifier=verifier,
        scopes=[str(x) for x in scopes],
        # RFC 8707: without it a provider may issue a token valid for more than this one server.
        resource=endpoint,
    )
    return login, url


def _register_oauth_routes(app: FastAPI, service: OAuthService) -> None:
    @app.get("/api/oauth/credentials")
    async def list_credentials() -> dict[str, Any]:
        """Stored tokens, as metadata. Never bytes."""
        return {
            "credentials": [
                {
                    "credential_id": m.credential_id,
                    "owner": m.owner,
                    "provider": m.provider,
                    "endpoint": m.endpoint,
                    "scopes": m.scopes,
                    "expires_at": m.expires_at,
                    "seconds_remaining": m.seconds_remaining,
                    "expired": m.expired,
                    "has_refresh_token": m.has_refresh_token,
                    "refreshed_at": m.refreshed_at,
                }
                for m in service.store.list_metadata()
            ]
        }

    @app.post("/api/oauth/login")
    async def start_login(request: Request) -> dict[str, Any]:
        """Begin a login. Returns the URL the portal should open in a popup."""
        body = await request.json()
        # The redirect URI is derived from the request rather than configured: it must match
        # byte-for-byte what the provider was told at registration, and a hand-set value that
        # drifts from the actual origin fails at the redirect with a provider-side error nobody
        # can read.
        redirect_uri = str(request.base_url).rstrip("/") + CALLBACK_PATH
        login, url = await prepare_login(body, redirect_uri, _owner_of(request))
        service.pending.add(login)
        return {"authorization_url": url, "state": login.state}

    @app.get(CALLBACK_PATH)
    async def callback(request: Request) -> Any:
        """Where the provider sends the person back."""
        params = request.query_params
        if error := params.get("error"):
            detail = params.get("error_description") or error
            return _close_window(f"The provider refused: {detail}", ok=False)

        state = params.get("state", "")
        code = params.get("code", "")
        login = service.pending.take(state)
        if login is None:
            # Unknown, replayed, or expired. All three are the same answer: this callback does not
            # belong to a login we started.
            return _close_window(
                "This login could not be matched to a request from this session. Start again.",
                ok=False,
            )
        if not code:
            return _close_window("The provider returned no authorization code.", ok=False)

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            try:
                tokens = await exchange_code(
                    login.metadata,
                    code=code,
                    verifier=login.verifier,
                    client_id=str(login.metadata.get("client_id", "")),
                    redirect_uri=login.redirect_uri,
                    client=client,
                )
            except PermissionError as exc:
                logger.warning("OAuth exchange failed for %s: %s", login.credential_id, exc)
                return _close_window(f"Token exchange failed: {exc}", ok=False)

        service.store.save(
            credential_id=login.credential_id,
            owner=login.owner,
            provider=str(login.metadata.get("issuer", "")) or login.endpoint,
            endpoint=login.endpoint,
            token_response=tokens,
            metadata={
                "client_id": login.metadata.get("client_id", ""),
                "token_endpoint": login.metadata.get("token_endpoint", ""),
                "revocation_endpoint": login.metadata.get("revocation_endpoint", ""),
            },
        )
        logger.info("OAuth credential %r stored for %s", login.credential_id, login.owner)
        return _close_window(f"Connected {login.credential_id}.", ok=True)

    @app.delete("/api/oauth/credentials/{credential_id}")
    async def delete_credential(credential_id: str, request: Request) -> dict[str, Any]:
        """Forget a token, and revoke it upstream where the provider supports revocation."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await service.store.delete(credential_id, _owner_of(request), client=client)

    @app.get("/auth/mcp/probe")
    async def probe(endpoint: str) -> dict[str, Any]:
        """Does this server speak OAuth, and where? Step 2 of the portal flow.

        Separate from starting a login so the page can tell someone *before* a popup opens that a
        server needs a client_id it cannot get by itself.
        """
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            try:
                metadata = await discover_metadata(endpoint, client=client)
            except LookupError as exc:
                return {"supported": False, "detail": str(exc)}
        return {
            "supported": True,
            "issuer": metadata.get("issuer", ""),
            "authorization_endpoint": metadata.get("authorization_endpoint", ""),
            "scopes_supported": metadata.get("scopes_supported", []),
            "supports_registration": bool(metadata.get("registration_endpoint")),
        }
