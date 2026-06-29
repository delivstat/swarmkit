"""Tests for OIDC verification (human→panel edge) and its wiring into panel auth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from swarmkit_control_plane import SqliteRegistry, create_app
from swarmkit_control_plane._oidc import OidcVerifier

_ISSUER = "https://idp.example.com"
_AUDIENCE = "swarmkit-control-plane"

# One keypair for the whole module (RSA keygen is slow).
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIV_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUB_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)


def _token(**overrides: Any) -> str:
    claims: dict[str, Any] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "alice@example.com",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    claims.update(overrides)
    return pyjwt.encode(claims, _PRIV_PEM, algorithm="RS256")


def _verifier() -> OidcVerifier:
    # Inject a fake JWKS client that returns our public key (no network).
    fake = SimpleNamespace(get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=_PUB_PEM))
    return OidcVerifier(issuer=_ISSUER, audience=_AUDIENCE, jwks_client=fake)


def test_valid_token_returns_subject() -> None:
    assert _verifier().verify(_token()) == "alice@example.com"


def test_expired_token_rejected() -> None:
    assert _verifier().verify(_token(exp=datetime.now(UTC) - timedelta(minutes=1))) is None


def test_wrong_audience_rejected() -> None:
    assert _verifier().verify(_token(aud="someone-else")) is None


def test_wrong_issuer_rejected() -> None:
    assert _verifier().verify(_token(iss="https://evil.example")) is None


def test_garbage_token_rejected() -> None:
    assert _verifier().verify("not-a-jwt") is None


def test_panel_accepts_oidc_operator(tmp_path: Path) -> None:
    registry = SqliteRegistry(tmp_path / "registry.sqlite")

    async def verify(endpoint: str, token_ref: str) -> dict[str, Any]:  # pragma: no cover
        return {}

    client = TestClient(create_app(registry, verify=verify, oidc=_verifier()))

    # No token → 401; a valid OIDC JWT → operator (full access).
    assert client.get("/instances").status_code == 401
    ok = client.get("/instances", headers={"Authorization": f"Bearer {_token()}"})
    assert ok.status_code == 200
    # OIDC operators can mutate (enroll), proving full-access authorization.
    created = client.post(
        "/instances",
        json={"name": "dc", "endpoint": "n/a", "connection": "poll"},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert created.status_code == 200
