"""Authorization-code with PKCE, and the provider metadata that makes it possible.

PKCE (RFC 7636) is what makes an authorization code safe to hand back through a browser: the client
sends a hash of a secret it invented, and redeems the code by producing the secret. A code stolen in
transit is then worthless to anyone who does not hold the verifier.

`state` is separate and does a different job: it ties the callback to the request that started it,
so somebody else's authorization cannot be walked into this workspace's session. Both are required
here; neither substitutes for the other.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

#: An unclaimed login is a dead entry holding a verifier. Ten minutes is longer than any human
#: takes to click through a consent screen and short enough that abandoned attempts do not pile up.
PENDING_TTL_S = 600.0


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_verifier() -> str:
    """A high-entropy secret, 43-128 chars per RFC 7636 §4.1."""
    return _b64url(secrets.token_bytes(64))


def challenge_for(verifier: str) -> str:
    """S256 challenge. Plain is permitted by the RFC and is not offered — it protects nothing."""
    return _b64url(hashlib.sha256(verifier.encode()).digest())


@dataclass
class PendingLogin:
    """One in-flight authorization, remembered between the redirect and the callback."""

    state: str
    verifier: str
    credential_id: str
    endpoint: str
    owner: str
    redirect_uri: str
    metadata: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created_at > PENDING_TTL_S


class PendingLogins:
    """In-memory, because a half-finished login is worth less than the complexity of persisting it.

    A restart mid-login costs one click. Persisting it would mean writing a verifier — a secret —
    to disk to save that click.
    """

    def __init__(self) -> None:
        self._items: dict[str, PendingLogin] = {}

    def add(self, login: PendingLogin) -> None:
        self._sweep()
        self._items[login.state] = login

    def take(self, state: str) -> PendingLogin | None:
        """Consume a pending login. Single-use: a replayed callback finds nothing."""
        self._sweep()
        return self._items.pop(state, None)

    def _sweep(self) -> None:
        for key in [k for k, v in self._items.items() if v.expired]:
            del self._items[key]


def _resource_metadata_url(header: str) -> str:
    """Pull `resource_metadata` out of a `WWW-Authenticate` header.

    The header is `Bearer param="value", param2="value"` — the scheme sits in front of the first
    parameter, so a naive `startswith` on the comma-split parts misses the one that matters, which
    is always first. Splitting on the scheme separator before the parameter list is the fix.
    """
    _, _, params = header.partition(" ")
    for raw in params.split(","):
        key, sep, value = raw.strip().partition("=")
        if sep and key.strip().lower() == "resource_metadata":
            return value.strip().strip('"')
    return ""


async def discover_metadata(endpoint: str, *, client: httpx.AsyncClient) -> dict[str, Any]:
    """Find the authorization server for an MCP endpoint.

    Two routes, in the order the MCP authorization spec expects:

    1. An unauthenticated request answers 401 with `WWW-Authenticate: Bearer resource_metadata=…`,
       naming the protected-resource document that names the authorization server.
    2. Failing that, the well-known authorization-server metadata (RFC 8414) at the endpoint's
       origin.

    Returning a dict rather than a typed object on purpose: providers add fields, and a strict model
    would reject a server for being newer than us.
    """
    resource_meta_url = ""
    try:
        probe = await client.get(endpoint, headers={"Accept": "application/json"})
        resource_meta_url = _resource_metadata_url(probe.headers.get("www-authenticate", ""))
    except httpx.HTTPError:
        pass

    if resource_meta_url:
        try:
            resource = (await client.get(resource_meta_url)).json()
            servers = resource.get("authorization_servers") or []
            if servers:
                return await _authorization_server_metadata(str(servers[0]), client=client)
        except (httpx.HTTPError, ValueError):
            pass

    origin = httpx.URL(endpoint)
    return await _authorization_server_metadata(
        str(origin.copy_with(path="", query=None, fragment=None)), client=client
    )


async def _authorization_server_metadata(
    issuer: str, *, client: httpx.AsyncClient
) -> dict[str, Any]:
    base = issuer.rstrip("/")
    for suffix in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/openid-configuration",
    ):
        try:
            resp = await client.get(base + suffix)
            if resp.status_code < 400:
                data = resp.json()
                if data.get("authorization_endpoint") and data.get("token_endpoint"):
                    return dict(data)
        except (httpx.HTTPError, ValueError):
            continue
    msg = (
        f"No OAuth metadata at {base}. The server may not support OAuth, or it may need its "
        f"authorization and token endpoints configured by hand."
    )
    raise LookupError(msg)


async def register_client(
    metadata: dict[str, Any], *, redirect_uri: str, client: httpx.AsyncClient
) -> str:
    """Obtain a client_id, registering dynamically when the provider supports it (RFC 7591).

    Most MCP authorization servers expect this: there is no developer portal where somebody
    pre-registers SwarmKit, so the client registers itself at first use. Returns "" when the
    provider advertises no registration endpoint — the caller then needs a configured client_id,
    and saying so is more useful than sending an empty one and being refused at the redirect.
    """
    endpoint = metadata.get("registration_endpoint")
    if not endpoint:
        return ""
    resp = await client.post(
        str(endpoint),
        json={
            "client_name": "SwarmKit",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        headers={"Accept": "application/json"},
    )
    if resp.status_code >= 400:
        msg = f"Dynamic client registration failed ({resp.status_code}): {resp.text[:200]}"
        raise PermissionError(msg)
    return str(resp.json().get("client_id", ""))


def authorization_url(
    metadata: dict[str, Any],
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    verifier: str,
    scopes: list[str] | None = None,
    resource: str = "",
) -> str:
    """The URL to send the person to."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge_for(verifier),
        "code_challenge_method": "S256",
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    if resource:
        # RFC 8707. Without it a provider may issue a token valid for more than the one server we
        # are connecting, which is a wider grant than the person agreed to.
        params["resource"] = resource
    return str(httpx.URL(metadata["authorization_endpoint"], params=params))


async def exchange_code(
    metadata: dict[str, Any],
    *,
    code: str,
    verifier: str,
    client_id: str,
    redirect_uri: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Redeem an authorization code. Returns the provider's token response verbatim."""
    resp = await client.post(
        metadata["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Accept": "application/json"},
    )
    if resp.status_code >= 400:
        msg = f"Token exchange failed ({resp.status_code}): {resp.text[:200]}"
        raise PermissionError(msg)
    return dict(resp.json())


async def refresh_token(
    metadata: dict[str, Any],
    *,
    refresh: str,
    client_id: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    """Exchange a refresh token for a new access token.

    Raises `PermissionError` when the provider refuses — that is the signal to park the run and ask
    a human, rather than to retry.
    """
    resp = await client.post(
        metadata["token_endpoint"],
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        },
        headers={"Accept": "application/json"},
    )
    if resp.status_code >= 400:
        msg = f"Refresh refused ({resp.status_code}): {resp.text[:200]}"
        raise PermissionError(msg)
    return dict(resp.json())
