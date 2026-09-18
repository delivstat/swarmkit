"""Fleet enrollment — the instance side of the register/join protocol (design 19).

An instance (``serve``) is the resource owner: it issues the scoped credential a fleet uses to call
it, keyed by a per-fleet **membership**. This package holds the credential primitives + the
membership store; the ``/fleet/*`` routes (Phase 2b) build on it. Phase 1 (``/fleet/state``) shipped
separately.
"""

from __future__ import annotations

from swarmkit_runtime.fleet._credentials import (
    SCOPES,
    Membership,
    Scope,
    fingerprint,
    mint_secret,
    scope_covers,
    secret_hash,
)
from swarmkit_runtime.fleet._factory import create_membership_store
from swarmkit_runtime.fleet._identity import (
    ACTOR_ASSERTION_TTL_S,
    FLEET_ID_PREFIX,
    actor_message,
    deploy_message,
    fleet_id_from_public_key,
    proof_message,
    verify_proof,
    verify_signature,
)
from swarmkit_runtime.fleet._store import MembershipStore

__all__ = [
    "ACTOR_ASSERTION_TTL_S",
    "FLEET_ID_PREFIX",
    "SCOPES",
    "Membership",
    "MembershipStore",
    "Scope",
    "actor_message",
    "create_membership_store",
    "deploy_message",
    "fingerprint",
    "fleet_id_from_public_key",
    "mint_secret",
    "proof_message",
    "scope_covers",
    "secret_hash",
    "verify_proof",
    "verify_signature",
]
