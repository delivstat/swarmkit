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
from swarmkit_runtime.oauth._client_store import CLIENT_TYPES, ClientStore

logger = logging.getLogger("swarmkit.oauth")

CALLBACK_PATH = "/auth/mcp/callback"


@dataclass
class OAuthService:
    """Login state for one serve process."""

    store: TokenStore
    pending: PendingLogins
    clients: ClientStore | None = None


def _owner_of(request: Request) -> str:
    """Whose token this will be.

    Unauthenticated serve yields the none-provider's default identity, which is the
    single-operator case. The owner is recorded either way, so turning auth on later does not
    silently merge one person's grants into a shared pool — the rows stay distinguishable, and a
    token obtained before auth was enabled keeps the owner it was obtained under.
    """
    identity = getattr(request.state, "identity", None)
    return str(getattr(identity, "client_id", "") or "local")


def _declared_credentials(request: Request) -> dict[str, Any]:
    """The workspace's `credentials` block, or empty when no workspace is loaded."""
    runtime = getattr(request.app.state, "runtime", None)
    workspace = getattr(runtime, "workspace", None)
    raw = getattr(getattr(workspace, "raw", None), "credentials", None) or {}
    if not hasattr(raw, "items"):
        return {}
    return {str(key): _as_mapping(value) for key, value in dict(raw).items()}


def _as_mapping(entry: Any) -> dict[str, Any]:
    """A credential entry as a plain dict.

    The resolved workspace holds these as generated pydantic models, not dicts — reading them as
    mappings silently yields nothing, which renders every connection as an unconfigured `global`
    one. Enum members (`identity`) are flattened to their values so the JSON says `per-user`
    rather than a Python repr.
    """
    if hasattr(entry, "model_dump"):
        data = dict(entry.model_dump(mode="json"))
    elif hasattr(entry, "keys"):
        data = dict(entry)
    else:
        return {}
    identity = data.get("identity")
    if identity is not None:
        data["identity"] = getattr(identity, "value", identity)
    return data


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
    body: dict[str, Any],
    redirect_uri: str,
    owner: str,
    *,
    clients: ClientStore | None = None,
) -> tuple[PendingLogin, str]:
    """Discover, register if needed, and build the authorization URL.

    Separate from the route so the discovery-and-registration path is testable without an app,
    which is where the fiddly parts are.

    Resolution order for the OAuth client identity:
      1. Body-supplied `client_id` wins (explicit over implicit). Logged at INFO so operators
         can spot drift from a persistent registration.
      2. Dynamic client registration via the provider's metadata.
      3. A persistent client registered via `POST /api/oauth/clients` (if the client store is
         bound). Carries a `client_secret` through to the token exchange when present.
    """
    credential_id = str(body.get("credential_id", "")).strip()
    endpoint = str(body.get("endpoint", "")).strip()
    if not credential_id or not endpoint:
        raise HTTPException(400, "credential_id and endpoint are required")

    client_secret: str | None = None
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            metadata = await discover_metadata(endpoint, client=client)
        except LookupError as exc:
            raise HTTPException(400, str(exc)) from exc

        client_id = str(body.get("client_id", "")).strip()
        stored = clients.get(endpoint) if clients else None
        if client_id and stored:
            logger.info(
                "OAuth login for %s: body client_id overrides persistent registration",
                endpoint,
            )
        if not client_id:
            try:
                client_id = await register_client(
                    metadata, redirect_uri=redirect_uri, client=client
                )
            except PermissionError as exc:
                if not stored:
                    raise HTTPException(400, str(exc)) from exc
        if not client_id and stored:
            client_id = stored.client_id
            client_secret = stored.client_secret
        if not client_id:
            raise HTTPException(
                400,
                "This provider does not support dynamic client registration. Register the "
                "OAuth client via POST /api/oauth/clients, or pass client_id in the login body.",
            )

    verifier = generate_verifier()
    login = PendingLogin(
        state=generate_verifier()[:32],
        verifier=verifier,
        credential_id=credential_id,
        endpoint=endpoint,
        owner=owner,
        redirect_uri=redirect_uri,
        metadata={
            **metadata,
            "client_id": client_id,
            # Kept in memory on the PendingLogin only; dropped once the window closes or the
            # callback consumes it. The authoritative home is the ClientStore.
            "client_secret": client_secret or "",
        },
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


def _register_oauth_routes(app: FastAPI, service: OAuthService) -> None:  # noqa: PLR0915
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

    @app.get("/api/oauth/my-credentials")
    async def my_credentials(request: Request) -> dict[str, Any]:
        """What *this caller* has connected — and nothing about anybody else.

        A deliberately separate route from ``/api/oauth/credentials`` rather than that listing
        filtered for non-admins. The operator inventory answers "who has connected what", which in
        a multi-user deployment is a roster: in some organisations more sensitive than any single
        connection. Filtering it in the browser would not help — the data is disclosed the moment
        it is serialised, and `view-source` is the whole exploit.

        **This handler takes no owner parameter.** The owner comes from the authenticated identity,
        there is no argument to tamper with, and no query it can express that names anyone else —
        not *permitted* to read another owner's rows, structurally unable to ask. A query string
        that tries is ignored, because nothing reads one.

        Declared connections are listed whether or not the caller has connected them, so the page
        can show what the agent will use as them. A `global` connection carries no personal state:
        its token belongs to whoever the operator designated, so `connected` is null rather than a
        misleading false. See design/details/per-caller-credential-delegation.md.
        """
        owner = _owner_of(request)
        declared = _declared_credentials(request)
        mine = {m.credential_id: m for m in service.store.list_metadata() if m.owner == owner}

        rows: list[dict[str, Any]] = []
        for credential_id, entry in sorted(declared.items()):
            config = entry.get("config") or {}
            identity = str(entry.get("identity") or "global")
            token = mine.get(credential_id) if identity == "per-user" else None
            rows.append(
                {
                    "credential_id": credential_id,
                    "source": str(entry.get("source", "")),
                    "identity": identity,
                    "endpoint": config.get("endpoint"),
                    # null for a global connection: it is not this person's to connect.
                    "connected": (token is not None) if identity == "per-user" else None,
                    "scopes": token.scopes if token else [],
                    "expires_at": token.expires_at if token else None,
                    "seconds_remaining": token.seconds_remaining if token else None,
                    "expired": token.expired if token else None,
                }
            )
        return {"owner": owner, "credentials": rows}

    @app.post("/api/oauth/login")
    async def start_login(request: Request) -> dict[str, Any]:
        """Begin a login. Returns the URL the portal should open in a popup."""
        body = await request.json()
        # The redirect URI is derived from the request rather than configured: it must match
        # byte-for-byte what the provider was told at registration, and a hand-set value that
        # drifts from the actual origin fails at the redirect with a provider-side error nobody
        # can read.
        redirect_uri = str(request.base_url).rstrip("/") + CALLBACK_PATH
        login, url = await prepare_login(
            body, redirect_uri, _owner_of(request), clients=service.clients
        )
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
                    client_secret=str(login.metadata.get("client_secret", "")) or None,
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

    @app.get("/api/oauth/clients")
    async def list_oauth_clients() -> dict[str, Any]:
        """Registered OAuth clients, scrubbed of secrets.

        See design/details/oauth-persistent-clients.md.
        """
        if service.clients is None:
            return {"clients": []}
        return {
            "clients": [
                {
                    "endpoint": c.endpoint,
                    "client_id": c.client_id,
                    "client_type": c.client_type,
                    "display_name": c.display_name,
                    "scopes": c.scopes,
                    "created_at": c.created_at,
                }
                for c in service.clients.list_display()
            ]
        }

    @app.post("/api/oauth/clients")
    async def register_oauth_client(request: Request) -> dict[str, Any]:
        """Register an OAuth client the runtime will use to drive logins against a provider that
        does not support dynamic client registration.
        """
        if service.clients is None:
            raise HTTPException(503, "persistent OAuth clients disabled (no workspace path)")
        body = await request.json()
        endpoint = str(body.get("endpoint", "")).strip()
        client_id = str(body.get("client_id", "")).strip()
        client_type = str(body.get("client_type", "")).strip() or "desktop"
        display_name = str(body.get("display_name", "")).strip() or endpoint
        secret = body.get("client_secret")
        scopes = body.get("scopes") or []
        if not endpoint or not client_id:
            raise HTTPException(400, "endpoint and client_id are required")
        if client_type not in CLIENT_TYPES:
            raise HTTPException(400, f"client_type must be one of {CLIENT_TYPES}")
        display = service.clients.save(
            endpoint=endpoint,
            client_id=client_id,
            client_secret=str(secret) if secret else None,
            client_type=client_type,
            display_name=display_name,
            scopes=[str(s) for s in scopes],
        )
        return {
            "endpoint": display.endpoint,
            "client_id": display.client_id,
            "client_type": display.client_type,
            "display_name": display.display_name,
            "scopes": display.scopes,
            "created_at": display.created_at,
        }

    @app.delete("/api/oauth/clients")
    async def delete_oauth_client(endpoint: str) -> dict[str, Any]:
        """Forget a registered OAuth client. Takes the endpoint as a query parameter so the
        URL-encoded form matches what POST /api/oauth/clients accepted.
        """
        if service.clients is None:
            raise HTTPException(503, "persistent OAuth clients disabled (no workspace path)")
        deleted = service.clients.delete(endpoint)
        return {"deleted": deleted}

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
