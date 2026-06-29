"""Minimal spec-conformant OIDC provider for the fleet-UI login e2e.

Serves discovery + JWKS + authorize (auto-approves → redirects back with a code) + token (issues a
signed id_token for oidc-client-ts and an access_token whose `aud` matches the panel). Enough for a
real browser PKCE round-trip; the PKCE verifier is accepted without checking (test fixture only).

Run from the repo root: `uv run python packages/control-plane-ui/e2e/fake-idp.py`.
"""

from __future__ import annotations

import json
import time

import jwt as pyjwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://127.0.0.1:8402"
CLIENT_ID = "swarmkit-fleet-ui"
API_AUDIENCE = "swarmkit-control-plane"
KID = "fake-idp-key"
UI_ORIGIN = "http://localhost:3000"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_jwk = {
    **json.loads(RSAAlgorithm.to_jwk(_key.public_key())),
    "kid": KID,
    "use": "sig",
    "alg": "RS256",
}
_codes: dict[str, str] = {}  # code -> nonce

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=[UI_ORIGIN], allow_methods=["*"], allow_headers=["*"]
)


@app.get("/.well-known/openid-configuration")
def discovery() -> dict[str, object]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@app.get("/jwks")
def jwks() -> dict[str, object]:
    return {"keys": [_jwk]}


@app.get("/authorize")
def authorize(redirect_uri: str, state: str, nonce: str = "") -> RedirectResponse:
    code = f"code-{int(time.time() * 1000)}"
    _codes[code] = nonce
    return RedirectResponse(url=f"{redirect_uri}?code={code}&state={state}", status_code=302)


def _sign(claims: dict[str, object]) -> str:
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": ISSUER,
        "sub": "alice@example.com",
        "iat": now,
        "exp": now + 3600,
        **claims,
    }
    return pyjwt.encode(payload, _key, algorithm="RS256", headers={"kid": KID})


@app.post("/token")
def token(code: str = Form(...)) -> JSONResponse:
    nonce = _codes.pop(code, "")
    return JSONResponse(
        {
            "access_token": _sign({"aud": API_AUDIENCE}),  # sent to the panel
            "id_token": _sign({"aud": CLIENT_ID, "nonce": nonce}),  # validated by oidc-client-ts
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid profile email",
        }
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8402, log_level="warning")
