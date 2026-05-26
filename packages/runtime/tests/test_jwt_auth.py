"""Tests for JWTAuthProvider — JWT bearer token authentication with JWKS."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from swarmkit_runtime.auth._jwt import JWTAuthProvider
from swarmkit_runtime.auth._provider import AuthError, AuthRequest

# ---------------------------------------------------------------------------
# Test RSA key pair (generated once per module)
# ---------------------------------------------------------------------------

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()

_ISSUER = "https://auth.example.com"
_AUDIENCE = "swarmkit"


def _public_key_jwk() -> dict[str, Any]:
    """Export the public key as a JWK dict."""
    from jwt.algorithms import RSAAlgorithm  # noqa: PLC0415

    jwk_str = RSAAlgorithm.to_jwk(_PUBLIC_KEY)
    jwk: dict[str, Any] = json.loads(jwk_str)
    jwk["kid"] = "test-key-1"
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


def _jwks_response() -> dict[str, Any]:
    """Build a JWKS response containing our test public key."""
    return {"keys": [_public_key_jwk()]}


def _encode_token(
    payload: dict[str, Any],
    *,
    headers: dict[str, Any] | None = None,
) -> str:
    """Encode a JWT with the test private key."""
    default_headers = {"kid": "test-key-1"}
    if headers:
        default_headers.update(headers)
    return pyjwt.encode(
        payload,
        _PRIVATE_KEY,
        algorithm="RS256",
        headers=default_headers,
    )


def _make_request(token: str | None = None) -> AuthRequest:
    """Build an AuthRequest with optional bearer token."""
    headers: dict[str, str] = {}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return AuthRequest(headers=headers, path="/run/hello", method="POST")


def _base_payload(**overrides: Any) -> dict[str, Any]:
    """Standard valid JWT payload with overridable claims."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "user-42",
        "name": "Alice Example",
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "exp": now + 3600,
        "iat": now,
        "scope": "topologies:run jobs:read",
    }
    payload.update(overrides)
    return payload


def _build_provider(
    *,
    scopes_claim: str = "scope",
) -> JWTAuthProvider:
    """Build a JWTAuthProvider with mocked JWKS fetch."""
    provider = JWTAuthProvider(
        issuer=_ISSUER,
        audience=_AUDIENCE,
        jwks_url=f"{_ISSUER}/.well-known/jwks.json",
        scopes_claim=scopes_claim,
    )
    # Replace the internal PyJWKClient with one that returns our test key.
    mock_client = MagicMock()
    mock_signing_key = MagicMock()
    mock_signing_key.key = _PUBLIC_KEY
    mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
    provider._jwks_client = mock_client
    return provider


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwt_valid_token() -> None:
    """Valid RS256 token authenticates successfully."""
    provider = _build_provider()
    token = _encode_token(_base_payload())
    request = _make_request(token)

    identity = await provider.authenticate(request)

    assert identity.client_id == "user-42"
    assert identity.client_name == "Alice Example"
    assert identity.provider == "jwt"
    assert "topologies:run" in identity.scopes
    assert "jobs:read" in identity.scopes


@pytest.mark.asyncio
async def test_jwt_expired_token() -> None:
    """Expired token raises AuthError."""
    provider = _build_provider()
    token = _encode_token(_base_payload(exp=int(time.time()) - 60))
    request = _make_request(token)

    with pytest.raises(AuthError, match="expired"):
        await provider.authenticate(request)


@pytest.mark.asyncio
async def test_jwt_wrong_issuer() -> None:
    """Token with wrong issuer raises AuthError."""
    provider = _build_provider()
    token = _encode_token(_base_payload(iss="https://evil.example.com"))
    request = _make_request(token)

    with pytest.raises(AuthError, match="issuer"):
        await provider.authenticate(request)


@pytest.mark.asyncio
async def test_jwt_wrong_audience() -> None:
    """Token with wrong audience raises AuthError."""
    provider = _build_provider()
    token = _encode_token(_base_payload(aud="wrong-audience"))
    request = _make_request(token)

    with pytest.raises(AuthError, match="audience"):
        await provider.authenticate(request)


@pytest.mark.asyncio
async def test_jwt_missing_header() -> None:
    """No Authorization header raises AuthError."""
    provider = _build_provider()
    request = _make_request(token=None)

    with pytest.raises(AuthError, match="Missing"):
        await provider.authenticate(request)


@pytest.mark.asyncio
async def test_jwt_scopes_extraction() -> None:
    """Scopes extracted from configurable claim."""
    provider = _build_provider(scopes_claim="permissions")
    token = _encode_token(_base_payload(permissions="admin:write admin:read"))
    request = _make_request(token)

    identity = await provider.authenticate(request)

    assert "admin:write" in identity.scopes
    assert "admin:read" in identity.scopes


@pytest.mark.asyncio
async def test_jwt_scopes_extraction_list() -> None:
    """Scopes extracted when claim is a list instead of space-separated string."""
    provider = _build_provider(scopes_claim="permissions")
    token = _encode_token(_base_payload(permissions=["admin:write", "admin:read"]))
    request = _make_request(token)

    identity = await provider.authenticate(request)

    assert "admin:write" in identity.scopes
    assert "admin:read" in identity.scopes


@pytest.mark.asyncio
async def test_jwt_authorize_with_scopes() -> None:
    """authorize checks scope match and wildcard."""
    provider = _build_provider()
    token = _encode_token(_base_payload())
    request = _make_request(token)

    identity = await provider.authenticate(request)

    assert await provider.authorize(identity, "topologies", "run") is True
    assert await provider.authorize(identity, "jobs", "read") is True
    assert await provider.authorize(identity, "admin", "write") is False


@pytest.mark.asyncio
async def test_jwt_authorize_wildcard() -> None:
    """Wildcard scope grants access to everything."""
    provider = _build_provider()
    token = _encode_token(_base_payload(scope="*"))
    request = _make_request(token)

    identity = await provider.authenticate(request)

    assert await provider.authorize(identity, "anything", "goes") is True


@pytest.mark.asyncio
async def test_jwt_client_name_fallback() -> None:
    """Tries name -> preferred_username -> email -> sub for display name."""
    provider = _build_provider()

    # Has name — use it
    token = _encode_token(_base_payload(name="Alice"))
    identity = await provider.authenticate(_make_request(token))
    assert identity.client_name == "Alice"

    # No name, has preferred_username
    payload_no_name = _base_payload()
    del payload_no_name["name"]
    payload_no_name["preferred_username"] = "alice42"
    token = _encode_token(payload_no_name)
    identity = await provider.authenticate(_make_request(token))
    assert identity.client_name == "alice42"

    # No name or preferred_username, has email
    payload_email = _base_payload()
    del payload_email["name"]
    payload_email["email"] = "alice@example.com"
    token = _encode_token(payload_email)
    identity = await provider.authenticate(_make_request(token))
    assert identity.client_name == "alice@example.com"

    # Nothing — falls back to sub
    payload_sub = _base_payload()
    del payload_sub["name"]
    token = _encode_token(payload_sub)
    identity = await provider.authenticate(_make_request(token))
    assert identity.client_name == "user-42"
