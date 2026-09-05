"""OAuth core — PKCE, the encrypted token store, and the properties that must not regress.

The security assertions here are the point. `mcp-oauth.md` makes three promises about refresh
tokens — encrypted at rest, never returned by a route, revoked upstream on delete — and each is
easy to break later with a plausible-looking convenience method.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from swarmkit_runtime.oauth import (
    KEY_ENV,
    PendingLogin,
    PendingLogins,
    SecretBox,
    SecretBoxError,
    TokenStore,
    authorization_url,
    challenge_for,
    discover_metadata,
    exchange_code,
    generate_verifier,
)
from swarmkit_runtime.oauth._pkce import PENDING_TTL_S

METADATA = {
    "authorization_endpoint": "https://auth.example/authorize",
    "token_endpoint": "https://auth.example/token",
}


# ---- PKCE ------------------------------------------------------------------------------------


def test_challenge_is_the_s256_of_the_verifier() -> None:
    verifier = generate_verifier()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert challenge_for(verifier) == expected


def test_verifier_is_high_entropy_and_within_the_rfc_length() -> None:
    seen = {generate_verifier() for _ in range(50)}
    assert len(seen) == 50
    assert all(43 <= len(v) <= 128 for v in seen)


def test_authorization_url_carries_challenge_never_the_verifier() -> None:
    """The verifier is the secret. Putting it in the URL would defeat the entire mechanism."""
    verifier = generate_verifier()
    url = authorization_url(
        METADATA,
        client_id="swarmkit",
        redirect_uri="http://127.0.0.1:8000/auth/mcp/callback",
        state="st-1",
        verifier=verifier,
        scopes=["read", "write"],
        resource="https://mcp.linear.app/mcp",
    )
    assert verifier not in url
    assert challenge_for(verifier) in url
    assert "code_challenge_method=S256" in url
    assert "scope=read+write" in url or "scope=read%20write" in url
    # RFC 8707: without it a provider may issue a token valid for more than this one server.
    assert "resource=" in url


# ---- pending logins --------------------------------------------------------------------------


def _pending(state: str = "st-1") -> PendingLogin:
    return PendingLogin(
        state=state,
        verifier=generate_verifier(),
        credential_id="linear",
        endpoint="https://mcp.linear.app/mcp",
        owner="srijith@delivstat.com",
        redirect_uri="http://127.0.0.1:8000/auth/mcp/callback",
        metadata=METADATA,
    )


def test_a_callback_is_single_use() -> None:
    """A replayed callback must find nothing — otherwise one intercepted code works twice."""
    logins = PendingLogins()
    logins.add(_pending())
    assert logins.take("st-1") is not None
    assert logins.take("st-1") is None


def test_an_unknown_state_finds_nothing() -> None:
    """The CSRF case: somebody else's authorization must not walk into this session."""
    logins = PendingLogins()
    logins.add(_pending())
    assert logins.take("not-the-state") is None


def test_an_abandoned_login_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    logins = PendingLogins()
    login = _pending()
    logins.add(login)
    monkeypatch.setattr("time.monotonic", lambda: login.created_at + PENDING_TTL_S + 1)
    assert logins.take("st-1") is None


# ---- the secret box --------------------------------------------------------------------------


def test_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    box = SecretBox.for_workspace(tmp_path)
    assert box.decrypt(box.encrypt("refresh-abc")) == "refresh-abc"


def test_the_key_persists_across_instances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An ephemeral key would silently invalidate every stored token on restart, which looks
    exactly like the provider revoking access. The fleet panel shipped that bug once."""
    monkeypatch.delenv(KEY_ENV, raising=False)
    cipher = SecretBox.for_workspace(tmp_path).encrypt("refresh-abc")
    assert SecretBox.for_workspace(tmp_path).decrypt(cipher) == "refresh-abc"


def test_the_key_file_is_not_world_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    SecretBox.for_workspace(tmp_path)
    mode = (tmp_path / ".swarmkit" / "oauth.key").stat().st_mode
    assert mode & 0o077 == 0


def test_a_wrong_key_says_what_to_do(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    cipher = SecretBox.for_workspace(tmp_path).encrypt("x")
    with pytest.raises(SecretBoxError, match=KEY_ENV):
        SecretBox(Fernet.generate_key()).decrypt(cipher)


# ---- the store -------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TokenStore:
    monkeypatch.delenv(KEY_ENV, raising=False)
    return TokenStore(tmp_path)


def _save(store: TokenStore, **overrides: Any) -> Any:
    payload = {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "scope": "read write",
    }
    payload.update(overrides)
    return store.save(
        credential_id="linear",
        owner="srijith@delivstat.com",
        provider="linear",
        endpoint="https://mcp.linear.app/mcp",
        token_response=payload,
    )


def test_metadata_cannot_leak_a_token(store: TokenStore) -> None:
    """The only accessor a route may call. It cannot leak, because it never reads the bytes."""
    meta = _save(store)
    rendered = str(meta.__dict__)
    assert "access-1" not in rendered
    assert "refresh-1" not in rendered
    assert meta.has_refresh_token is True
    assert meta.scopes == ["read", "write"]


def test_tokens_are_encrypted_on_disk(store: TokenStore, tmp_path: Path) -> None:
    _save(store)
    raw = (tmp_path / ".swarmkit" / "state" / "oauth.db").read_bytes()
    assert b"access-1" not in raw
    assert b"refresh-1" not in raw


def test_plaintext_is_available_to_the_runtime(store: TokenStore) -> None:
    _save(store)
    assert store.access_token("linear", "srijith@delivstat.com") == "access-1"
    assert store.refresh_token("linear", "srijith@delivstat.com") == "refresh-1"


def test_a_refresh_without_a_new_refresh_token_keeps_the_old_one(store: TokenStore) -> None:
    """Providers usually omit it, meaning *keep using the one you have*. Writing NULL would
    discard a standing grant and force a fresh login at the next expiry."""
    _save(store)
    store.save(
        credential_id="linear",
        owner="srijith@delivstat.com",
        provider="linear",
        endpoint="https://mcp.linear.app/mcp",
        token_response={"access_token": "access-2", "expires_in": 3600},
    )
    assert store.access_token("linear", "srijith@delivstat.com") == "access-2"
    assert store.refresh_token("linear", "srijith@delivstat.com") == "refresh-1"


def test_owner_is_part_of_the_key(store: TokenStore) -> None:
    """One person's login must not silently become everyone's."""
    _save(store)
    store.save(
        credential_id="linear",
        owner="someone-else@example.com",
        provider="linear",
        endpoint="https://mcp.linear.app/mcp",
        token_response={"access_token": "other-access", "refresh_token": "other-refresh"},
    )
    assert store.access_token("linear", "srijith@delivstat.com") == "access-1"
    assert store.access_token("linear", "someone-else@example.com") == "other-access"
    assert len(store.list_metadata()) == 2


def test_expiry_uses_a_skew_so_a_token_does_not_die_mid_call(store: TokenStore) -> None:
    fresh = _save(store, expires_in=3600)
    assert fresh.expired is False
    nearly = _save(store, expires_in=30)
    assert nearly.expired is True


def test_a_response_without_an_access_token_is_refused(store: TokenStore) -> None:
    with pytest.raises(ValueError, match="no access_token"):
        _save(store, access_token="")


@pytest.mark.asyncio
async def test_delete_removes_the_row_even_when_revocation_fails(store: TokenStore) -> None:
    """Leaving the row because someone else's server was unreachable would be a delete that did
    nothing visible."""
    _save(store)
    result = await store.delete("linear", "srijith@delivstat.com")
    assert result["deleted"] is True
    assert store.metadata("linear", "srijith@delivstat.com") is None


# ---- discovery and exchange ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_follows_the_401_to_the_authorization_server() -> None:
    """The MCP authorization route: an unauthenticated probe names the resource metadata, which
    names the authorization server."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://mcp.example/mcp":
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource"'
                },
            )
        if url.endswith("oauth-protected-resource"):
            return httpx.Response(200, json={"authorization_servers": ["https://auth.example"]})
        if url == "https://auth.example/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=METADATA)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        meta = await discover_metadata("https://mcp.example/mcp", client=client)
    assert meta["token_endpoint"] == METADATA["token_endpoint"]


@pytest.mark.asyncio
async def test_discovery_says_what_to_do_when_there_is_no_metadata() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as client:
        with pytest.raises(LookupError, match="may not support OAuth"):
            await discover_metadata("https://mcp.example/mcp", client=client)


@pytest.mark.asyncio
async def test_exchange_sends_the_verifier_and_returns_the_response() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await exchange_code(
            METADATA,
            code="the-code",
            verifier="the-verifier",
            client_id="swarmkit",
            redirect_uri="http://127.0.0.1/cb",
            client=client,
        )
    assert out["access_token"] == "a"
    assert seen["code_verifier"] == "the-verifier"
    assert seen["grant_type"] == "authorization_code"


@pytest.mark.asyncio
async def test_a_refused_exchange_raises_rather_than_returning_empty() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(400, text="bad_verifier"))
    ) as client:
        with pytest.raises(PermissionError, match="bad_verifier"):
            await exchange_code(
                METADATA,
                code="c",
                verifier="v",
                client_id="swarmkit",
                redirect_uri="http://127.0.0.1/cb",
                client=client,
            )
