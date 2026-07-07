"""Fleet enrollment — the instance side of the register/join protocol (design 19).

An instance (``serve``) is the resource owner: it issues the scoped credential a fleet uses to call
it, keyed by a per-fleet **membership**. This package holds the credential primitives + the
membership store; the ``/fleet/*`` routes (Phase 2b) build on it. Phase 1 (``/fleet/state``) shipped
separately.
"""

from __future__ import annotations

from swarmkit_runtime.fleet._credentials import (
    Membership,
    Scope,
    fingerprint,
    mint_secret,
    secret_hash,
)
from swarmkit_runtime.fleet._store import MembershipStore

__all__ = [
    "Membership",
    "MembershipStore",
    "Scope",
    "fingerprint",
    "mint_secret",
    "secret_hash",
]
