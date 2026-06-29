"""OIDC verification for the human→panel auth edge (design/details/control-plane/12-auth.md §3).

The panel is an OIDC relying party: an operator (or the fleet UI on their behalf) presents a JWT
issued by the org IdP, and the panel validates it against the issuer's JWKS. Mirrors serve's
``JWTAuthProvider`` (RS256/ES256 + JWKS auto-discovery, validate iss/aud/exp/sub) so one IdP setup
serves both edges. A valid token authenticates the caller as an operator.

PyJWT is imported lazily so the package still imports where OIDC isn't configured.
"""

from __future__ import annotations

from typing import Any, Protocol


class _JwksClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class OidcVerifier:
    """Validates OIDC JWTs against an issuer's JWKS and returns the subject."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        jwks_client: _JwksClient | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        # Default JWKS URL follows the OIDC discovery convention (same as serve).
        self.jwks_url = jwks_url or f"{issuer.rstrip('/')}/.well-known/jwks.json"
        self._jwks_client = jwks_client  # injectable for tests; lazily built otherwise

    def _client(self) -> _JwksClient:
        if self._jwks_client is None:
            from jwt import PyJWKClient  # noqa: PLC0415 — lazy: only when OIDC is configured

            self._jwks_client = PyJWKClient(self.jwks_url)
        return self._jwks_client

    def verify(self, token: str) -> str | None:
        """Return the validated subject (``sub``), or None if the token is invalid."""
        import jwt as pyjwt  # noqa: PLC0415

        try:
            signing_key = self._client().get_signing_key_from_jwt(token).key
            payload: dict[str, Any] = pyjwt.decode(
                token,
                signing_key,
                algorithms=["RS256", "ES256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except (pyjwt.InvalidTokenError, pyjwt.PyJWKClientError):
            return None
        sub = payload.get("sub")
        return str(sub) if sub else None
