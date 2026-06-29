"""Panel authentication — who may call the control-plane API.

Two principals (design/details/control-plane/12-auth.md §3 "two auth edges"):

- **operator** — a human/automation managing the fleet. Holds an operator token (config) and may
  call every route. (Human→panel OIDC login is a later slice; operator tokens are the M2M stand-in.)
- **connector** — a Mode B instance polling the panel with its per-instance minted token
  (§3 "the connector credential"). Scoped to *its own* poll + command-result routes only.

A connector is identified by hashing the presented bearer and matching it to the `token_hash`
recorded at mint — nothing reversible is stored, and the token is high-entropy so a plain hash
suffices. Auth is enforced only when operator tokens are configured; otherwise the panel runs open
(local dev). Authorization is deny-by-default for connectors.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable
from dataclasses import dataclass

from swarmkit_control_plane._oidc import OidcVerifier
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._tokens import token_hash


@dataclass(frozen=True)
class Principal:
    kind: str  # "operator" | "connector"
    instance_id: str | None = None  # set for connectors
    subject: str | None = None  # OIDC subject, for operators authenticated via a JWT


def authenticate(
    token: str,
    operator_tokens: Iterable[str],
    registry: SqliteRegistry,
    oidc: OidcVerifier | None = None,
) -> Principal | None:
    """Resolve a bearer token to a Principal, or None if it matches nothing.

    Order: static operator token, then a Mode B instance's connector token (by hash), then an
    OIDC JWT (human→panel). The first two are cheap local checks; OIDC is tried last (it may hit
    the JWKS cache). A valid OIDC token authenticates as an operator carrying its subject.
    """
    if not token:
        return None
    for op in operator_tokens:
        if op and hmac.compare_digest(token, op):
            return Principal("operator")
    inst = registry.get_by_token_hash(token_hash(token))
    if inst is not None:
        return Principal("connector", inst.id)
    if oidc is not None:
        subject = oidc.verify(token)
        if subject is not None:
            return Principal("operator", subject=subject)
    return None


def authorize(principal: Principal, method: str, path: str) -> bool:
    """Is *principal* allowed to call method+path? Operators: everything; connectors: own routes."""
    if principal.kind == "operator":
        return True
    iid = principal.instance_id
    if not iid:
        return False
    # A connector may push its own observability signals (instance_id is taken from the principal,
    # so it can't push as another instance — see the aggregation handler).
    if path.startswith("/aggregate/"):
        return True
    # ...and long-poll + report results for its own instance.
    if path == f"/instances/{iid}/poll":
        return True
    return path.startswith(f"/instances/{iid}/commands/") and path.endswith("/result")
