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
from swarmkit_runtime.fleet._identity import (
    FLEET_ID_PREFIX,
    fleet_id_from_public_key,
    proof_message,
    verify_proof,
)
from swarmkit_runtime.fleet._store import MembershipStore

__all__ = [
    "FLEET_ID_PREFIX",
    "Membership",
    "MembershipStore",
    "Scope",
    "fingerprint",
    "fleet_id_from_public_key",
    "mint_secret",
    "proof_message",
    "secret_hash",
    "verify_proof",
]
