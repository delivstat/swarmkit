"""AuthProvider abstraction for SwarmKit server authentication.

Mirrors the GovernanceProvider pattern (design §8.5): a narrow ABC that
the server depends on, with concrete implementations swapped at startup.

All methods are async — auth checks may involve I/O (e.g. JWKS fetch,
database lookup) in future providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuthRequest:
    """Incoming HTTP request distilled to auth-relevant fields."""

    headers: dict[str, str]
    path: str
    method: str
    query_params: dict[str, str] = field(default_factory=dict)
    client_ip: str | None = None


@dataclass(frozen=True)
class AuthIdentity:
    """Authenticated caller identity attached to request state."""

    client_id: str
    client_name: str
    provider: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    metadata: dict[str, Any] = field(default_factory=dict)


class AuthError(Exception):
    """Raised when authentication or authorization fails."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthProvider(ABC):
    """Narrow, stable interface for HTTP-level authentication.

    Concrete implementations: NoneAuthProvider (open access),
    APIKeyAuthProvider (bearer token), future: JWTAuthProvider, OIDCAuthProvider.
    """

    @abstractmethod
    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        """Verify the caller's identity from request headers/params.

        Raises AuthError if the caller cannot be identified.
        """

    @abstractmethod
    async def authorize(self, identity: AuthIdentity, resource: str, action: str) -> bool:
        """Check whether *identity* may perform *action* on *resource*.

        Returns True if allowed, False if denied.
        """

    @property
    def mode(self) -> str:
        """Short public identifier for this auth mode: ``none`` | ``api_key`` | ``jwt``.

        Advertised (unauthenticated) via ``GET /auth-info`` so a client knows which login gate to
        render before it holds a token. Overridden by each concrete provider."""
        return "unknown"

    def public_info(self) -> dict[str, Any]:
        """Unauthenticated descriptor a client reads to render the right login gate. Default is just
        the mode; providers advertise extra config (e.g. an OIDC issuer/audience) by overriding."""
        return {"mode": self.mode}
