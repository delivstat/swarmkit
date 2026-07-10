"""NoneAuthProvider — open-access default.

Used when no auth is configured. Every request gets an anonymous identity
with wildcard scopes. Suitable for local development and trusted networks.
"""

from __future__ import annotations

from swarmkit_runtime.auth._provider import AuthIdentity, AuthProvider, AuthRequest

_ANONYMOUS = AuthIdentity(
    client_id="anonymous",
    client_name="Anonymous",
    provider="none",
    scopes=frozenset(["*"]),
)


class NoneAuthProvider(AuthProvider):
    """No-op auth: always authenticates, always authorizes."""

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        return _ANONYMOUS

    async def authorize(self, identity: AuthIdentity, resource: str, action: str) -> bool:
        return True

    @property
    def mode(self) -> str:
        return "none"
