"""JWTAuthProvider — JWT bearer token authentication with JWKS auto-discovery.

Validates RS256/ES256 tokens from any OIDC-compliant issuer.  JWKS keys
are fetched and cached on first use; a cache miss triggers a single
background refresh so that key rotation is transparent.

Requires ``PyJWT[crypto]`` — installed automatically via the ``serve``
extra (``pip install 'swarmkit[serve]'``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from swarmkit_runtime.auth._provider import (
    AuthError,
    AuthIdentity,
    AuthProvider,
    AuthRequest,
)

try:
    import jwt as pyjwt
    from jwt import PyJWKClient
except ImportError as _imp_err:
    raise ImportError(
        "JWTAuthProvider requires PyJWT. Install with: pip install 'swarmkit[serve]'"
    ) from _imp_err


class JWTAuthProvider(AuthProvider):
    """JWT bearer token authentication with JWKS auto-discovery.

    Validates RS256/ES256 tokens from any OIDC-compliant issuer.
    JWKS keys are fetched and cached on first use.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str = "swarmkit",
        jwks_url: str | None = None,
        scopes_claim: str = "scope",
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._scopes_claim = scopes_claim

        # Default JWKS URL follows OIDC discovery convention.
        resolved_jwks_url = jwks_url or f"{issuer.rstrip('/')}/.well-known/jwks.json"
        self._jwks_url = resolved_jwks_url

        # PyJWKClient handles fetch + in-memory caching.
        self._jwks_client = PyJWKClient(self._jwks_url)

    # ------------------------------------------------------------------
    # AuthProvider interface
    # ------------------------------------------------------------------

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        token = self._extract_bearer(request.headers)
        if token is None:
            raise AuthError("Missing or invalid Authorization header", 401)

        try:
            # PyJWKClient does a *blocking*, network-bound fetch (JWKS discovery / refresh);
            # offload it so a slow IdP can't stall the whole event loop on every request.
            signing_key = await asyncio.to_thread(self._jwks_client.get_signing_key_from_jwt, token)
        except pyjwt.PyJWKClientError as exc:
            raise AuthError(f"JWKS key resolution failed: {exc}", 401) from exc

        try:
            payload: dict[str, Any] = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except pyjwt.ExpiredSignatureError as exc:
            raise AuthError("Token has expired", 401) from exc
        except pyjwt.InvalidIssuerError as exc:
            raise AuthError("Invalid token issuer", 401) from exc
        except pyjwt.InvalidAudienceError as exc:
            raise AuthError("Invalid token audience", 401) from exc
        except pyjwt.InvalidTokenError as exc:
            raise AuthError(f"Invalid token: {exc}", 401) from exc

        client_id = str(payload["sub"])
        client_name = self._resolve_client_name(payload)
        scopes = self._extract_scopes(payload)

        return AuthIdentity(
            client_id=client_id,
            client_name=client_name,
            provider="jwt",
            scopes=frozenset(scopes),
            metadata={"issuer": self._issuer},
        )

    async def authorize(self, identity: AuthIdentity, resource: str, action: str) -> bool:
        if "*" in identity.scopes:
            return True
        required = f"{resource}:{action}"
        return required in identity.scopes

    @property
    def mode(self) -> str:
        return "jwt"

    def public_info(self) -> dict[str, Any]:
        # issuer + audience are what a browser needs (with its own client_id) to run the OIDC PKCE
        # flow; jwks_url + scopes_claim are server-side validation details a client never sees.
        return {"mode": "jwt", "oidc": {"issuer": self._issuer, "audience": self._audience}}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_bearer(headers: dict[str, str]) -> str | None:
        """Extract bearer token from Authorization header."""
        auth = headers.get("authorization") or headers.get("Authorization")
        if auth is None:
            return None
        parts = auth.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1]

    @staticmethod
    def _resolve_client_name(payload: dict[str, Any]) -> str:
        """Try name -> preferred_username -> email -> sub for display name."""
        for claim in ("name", "preferred_username", "email"):
            value = payload.get(claim)
            if value:
                return str(value)
        return str(payload["sub"])

    def _extract_scopes(self, payload: dict[str, Any]) -> list[str]:
        """Extract scopes from the configured claim."""
        raw = payload.get(self._scopes_claim, "")
        if isinstance(raw, list):
            return [str(s) for s in raw]
        if isinstance(raw, str) and raw:
            return raw.split()
        return []
