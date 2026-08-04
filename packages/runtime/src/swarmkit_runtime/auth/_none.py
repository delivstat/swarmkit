"""NoneAuthProvider — open-access default.

Used when no auth is configured. Every request gets one identity with wildcard scopes. Suitable for
local development and trusted networks; the server refuses a non-loopback bind under this provider.

That identity may be **named**. This mode already trusts the caller absolutely — wildcard scopes and
an ``authorize()`` that returns True for everything — so nothing is withheld except a name, and the
name is the one thing the approval engine actually checks: it matches ``client_id`` against
``members:`` in ``swarm/roles.yaml``, not scopes. ``anonymous`` was therefore refused at a gate not
for lacking authority but for being nobody, which left a mode that permits every action unable to
perform the one action that depends on identity — and pushed local operators onto the break-glass
event path instead of the authenticated one.

Naming the operator grants no capability this mode does not already give. It is still an
**assertion**, not an authentication: the identity carries ``provider="none"`` so an audit reader
can tell "srijith approved this, verified by an IdP" from "someone on the loopback interface claimed
to be srijith".
"""

from __future__ import annotations

from swarmkit_runtime.auth._provider import AuthIdentity, AuthProvider, AuthRequest

DEFAULT_IDENTITY = "anonymous"

_ANONYMOUS = AuthIdentity(
    client_id=DEFAULT_IDENTITY,
    client_name="Anonymous",
    provider="none",
    scopes=frozenset(["*"]),
)


class NoneAuthProvider(AuthProvider):
    """No-op auth: always authenticates, always authorizes.

    ``identity`` is who the caller is asserted to be. It defaults to ``anonymous``, so an existing
    workspace behaves exactly as before.
    """

    def __init__(self, identity: str = DEFAULT_IDENTITY, identity_name: str = "") -> None:
        resolved = (identity or DEFAULT_IDENTITY).strip() or DEFAULT_IDENTITY
        self._identity = (
            _ANONYMOUS
            if resolved == DEFAULT_IDENTITY and not identity_name.strip()
            else AuthIdentity(
                client_id=resolved,
                client_name=identity_name.strip() or resolved,
                provider="none",
                scopes=frozenset(["*"]),
            )
        )

    @property
    def identity(self) -> AuthIdentity:
        """The asserted identity every request gets. Read by the startup role-membership check."""
        return self._identity

    async def authenticate(self, request: AuthRequest) -> AuthIdentity:
        return self._identity

    async def authorize(self, identity: AuthIdentity, resource: str, action: str) -> bool:
        return True

    @property
    def mode(self) -> str:
        return "none"
