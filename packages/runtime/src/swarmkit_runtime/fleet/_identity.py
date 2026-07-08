"""Fleet-identity crypto — a self-certifying ``fleet_id`` from an Ed25519 public key + verification
of a fleet's proof-of-possession at register (design 21).

Serve *verifies*; the panel (fleet) *signs* (with its private key). The ``fleet_id`` derivation and
the proof-message construction are a documented contract both sides must agree on byte-for-byte —
the panel re-implements the same two pure functions (contract-not-shared-code), pinned by a
cross-package test. No new dependency: ``cryptography`` already ships (Fernet).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FLEET_ID_PREFIX = "fleet:"


def fleet_id_from_public_key(public_key_b64: str) -> str:
    """Derive the self-certifying ``fleet_id`` from a base64 Ed25519 public key: the lowercased,
    unpadded base32 of ``sha256(pubkey_raw)``, prefixed ``fleet:``. Deterministic — the same key
    always yields the same id, and a different key a different id (so the id can't be forged)."""
    raw = base64.b64decode(public_key_b64, validate=True)
    digest = hashlib.sha256(raw).digest()
    return FLEET_ID_PREFIX + base64.b32encode(digest).decode("ascii").rstrip("=").lower()


def proof_message(enrollment_token: str, workspace_id: str) -> bytes:
    """The exact bytes a fleet signs to prove possession: ``<enrollment_token>:<workspace_id>``. The
    token gives freshness (single-use + TTL); the workspace id binds the proof to *this* instance,
    so a proof minted for one instance can't be replayed at another in the token's window (design
    21)."""
    return f"{enrollment_token}:{workspace_id}".encode()


def deploy_message(kind: str, artifact_id: str, content_hash: str) -> bytes:
    """The exact bytes a fleet signs to authorize a deploy: ``deploy:<kind>:<id>:<content_hash>``
    (design 22). Binding kind + id + content_hash means a stolen membership key can't push an
    artifact the fleet didn't sign, and a signature for one artifact can't be replayed onto
    another. ``content_hash`` is the registry canonicalisation both sides compute."""
    return f"deploy:{kind}:{artifact_id}:{content_hash}".encode()


def verify_signature(public_key_b64: str, signature_b64: str, message: bytes) -> bool:
    """True iff *signature_b64* is a valid Ed25519 signature by *public_key_b64* over *message*.
    Never raises — a malformed key/signature is simply not valid."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        pub.verify(base64.b64decode(signature_b64, validate=True), message)
    except (InvalidSignature, ValueError):
        return False
    return True


def verify_proof(
    public_key_b64: str, proof_b64: str, enrollment_token: str, workspace_id: str
) -> bool:
    """True iff *proof_b64* is a valid signature over ``proof_message(enrollment_token,
    workspace_id)`` (design 21)."""
    msg = proof_message(enrollment_token, workspace_id)
    return verify_signature(public_key_b64, proof_b64, msg)


__all__ = [
    "FLEET_ID_PREFIX",
    "deploy_message",
    "fleet_id_from_public_key",
    "proof_message",
    "verify_proof",
    "verify_signature",
]
