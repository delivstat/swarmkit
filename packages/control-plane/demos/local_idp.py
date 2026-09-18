"""A one-file OIDC provider for demos — never for anything else.

Discovery, ``/authorize`` (signs in whoever ``IDP_USER`` names, no password), ``/token`` (RS256
JWTs), JWKS, and ``/mint?sub=`` for curl. Enough for oidc-client-ts (the fleet UI) and the panel's
verifier, so Level 22's "approve as yourself" runs end to end on a laptop:

    IDP_USER=alice IDP_ISSUER=http://127.0.0.1:8556 uv run uvicorn local_idp:app \\
        --app-dir packages/control-plane/demos --port 8556
    swarmkit-control-plane … --oidc-issuer http://127.0.0.1:8556 --oidc-audience swarmkit-fleet
    NEXT_PUBLIC_OIDC_AUTHORITY=http://127.0.0.1:8556 NEXT_PUBLIC_OIDC_CLIENT_ID=swarmkit-fleet-ui \\
        NEXT_PUBLIC_OIDC_AUDIENCE=swarmkit-fleet pnpm dev   # in packages/control-plane-ui

The signing key is written next to this file on first start (gitignored, ``*.pem``) so a restart
as another user keeps the panel's cached JWKS valid. Design: design/details/control-plane/28.
"""

from __future__ import annotations

import base64
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

ISS = os.environ.get("IDP_ISSUER", "http://127.0.0.1:8556")
USER = os.environ.get("IDP_USER", "alice")
AUD = os.environ.get("IDP_AUDIENCE", "swarmkit-fleet")
KEY_FILE = Path(os.environ.get("IDP_KEY_FILE", str(Path(__file__).with_name("idp-key.pem"))))
KID = "demo-1"


def _load_key() -> rsa.RSAPrivateKey:
    if KEY_FILE.exists():
        loaded = serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
        if not isinstance(loaded, rsa.RSAPrivateKey):
            raise TypeError(f"{KEY_FILE} is not an RSA key")
        return loaded
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_FILE.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return key


KEY = _load_key()
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
codes: dict[str, dict[str, Any]] = {}
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _b64(n: int) -> str:
    return (
        base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()
    )


@app.get("/.well-known/openid-configuration")
def discovery() -> dict[str, Any]:
    return {
        "issuer": ISS,
        "authorization_endpoint": f"{ISS}/authorize",
        "token_endpoint": f"{ISS}/token",
        "jwks_uri": f"{ISS}/.well-known/jwks.json",
        "end_session_endpoint": f"{ISS}/logout",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "profile", "email"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/.well-known/jwks.json")
def jwks() -> dict[str, Any]:
    pub = KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": KID,
                "n": _b64(pub.n),
                "e": _b64(pub.e),
            }
        ]
    }


@app.get("/authorize")
def authorize(request: Request) -> RedirectResponse:
    q = request.query_params
    code = secrets.token_urlsafe(16)
    codes[code] = {"client_id": q.get("client_id"), "nonce": q.get("nonce"), "user": USER}
    query = urlencode({"code": code, "state": q.get("state", "")})
    return RedirectResponse(f"{q['redirect_uri']}?{query}")


def _token(
    sub: str, aud: str, *, nonce: str | None = None, extra: dict[str, Any] | None = None
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISS,
        "sub": sub,
        "aud": aud,
        "iat": now,
        "exp": now + 3600,
        "jti": uuid.uuid4().hex,
        **(extra or {}),
    }
    if nonce:
        claims["nonce"] = nonce
    return jwt.encode(claims, PEM, algorithm="RS256", headers={"kid": KID})


@app.post("/token")
def token(code: str = Form(...), client_id: str | None = Form(None)) -> dict[str, Any]:
    c = codes.pop(code)
    sub = str(c["user"])
    return {
        "token_type": "Bearer",
        "expires_in": 3600,
        "access_token": _token(sub, AUD, extra={"scope": "openid profile email"}),
        "id_token": _token(
            sub,
            str(c["client_id"] or client_id),
            nonce=c["nonce"],
            extra={"name": sub.title(), "email": f"{sub}@example.com"},
        ),
    }


@app.get("/logout")
def logout(post_logout_redirect_uri: str = "/") -> RedirectResponse:
    return RedirectResponse(post_logout_redirect_uri)


@app.get("/mint")
def mint(sub: str = USER) -> dict[str, str]:
    """A bare access token for curl demos."""
    return {"access_token": _token(sub, AUD)}
