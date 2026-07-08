"""FleetIdentity — this panel's own Ed25519 fleet identity (design 21).

The panel (the fleet) proves *who it is* to an instance at register by signing the enrollment token
with its private key; the instance pins the matching public key. The **`fleet_id` is derived from
the public key** (`fleet:<base32 sha256 pubkey>`), so it is self-certifying — it can't be forged
without the private key.

The private key is stored **encrypted at rest** via the panel's ``SecretBox`` (Fernet local /
Vault-Transit), the same seam that protects membership credentials; the public key + fleet_id are
non-secret. One keypair per panel (a singleton row), generated on first use.

The ``fleet_id`` derivation + proof-message construction must match serve's byte-for-byte — serve
re-implements the same pure functions (contract-not-shared-code, doc 21); a cross-package test pins
the shared vector.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select

from swarmkit_control_plane._secret_box import SecretBox, default_secret_box
from swarmkit_control_plane._store_base import Store
from swarmkit_control_plane._tables import fleet_identity as _identity_table

FLEET_ID_PREFIX = "fleet:"
_SINGLETON = "default"


def fleet_id_from_public_key(public_key_b64: str) -> str:
    """Derive the self-certifying ``fleet_id`` from a base64 Ed25519 public key. Must match serve's
    ``swarmkit_runtime.fleet.fleet_id_from_public_key`` byte-for-byte (contract, doc 21)."""
    raw = base64.b64decode(public_key_b64, validate=True)
    digest = hashlib.sha256(raw).digest()
    return FLEET_ID_PREFIX + base64.b32encode(digest).decode("ascii").rstrip("=").lower()


def proof_message(enrollment_token: str, workspace_id: str) -> bytes:
    """The bytes the fleet signs to prove possession — must match serve's ``proof_message``."""
    return f"{enrollment_token}:{workspace_id}".encode()


def deploy_message(kind: str, artifact_id: str, content_hash: str) -> bytes:
    """The bytes the fleet signs to authorize a deploy — must match serve's ``deploy_message``
    (``deploy:<kind>:<id>:<content_hash>``, design 22)."""
    return f"deploy:{kind}:{artifact_id}:{content_hash}".encode()


class FleetIdentity(Store):
    """The panel's persistent Ed25519 fleet identity (private key encrypted at rest)."""

    def __init__(
        self, backing: Any, secret_box: SecretBox | None = None, display_name: str = ""
    ) -> None:
        super().__init__(backing)
        self._box = secret_box or default_secret_box()
        self._load_or_create(display_name)

    def _load_or_create(self, display_name: str) -> None:
        with self._lock, self._engine.begin() as conn:
            row = (
                conn.execute(select(_identity_table).where(_identity_table.c.id == _SINGLETON))
                .mappings()
                .first()
            )
            if row is not None:
                self._public_key_b64 = str(row["public_key"])
                self._display_name = str(row["display_name"])
                priv_b64 = self._box.decrypt(str(row["private_key_ciphertext"]))
                self._private_key = Ed25519PrivateKey.from_private_bytes(
                    base64.b64decode(priv_b64, validate=True)
                )
            else:
                self._private_key = Ed25519PrivateKey.generate()
                pub_raw = self._private_key.public_key().public_bytes_raw()
                self._public_key_b64 = base64.b64encode(pub_raw).decode("ascii")
                self._display_name = display_name
                priv_b64 = base64.b64encode(self._private_key.private_bytes_raw()).decode("ascii")
                conn.execute(
                    _identity_table.insert().values(
                        id=_SINGLETON,
                        public_key=self._public_key_b64,
                        private_key_ciphertext=self._box.encrypt(priv_b64),
                        display_name=display_name,
                        created_at=datetime.now(UTC).isoformat(),
                    )
                )
        self._fleet_id = fleet_id_from_public_key(self._public_key_b64)

    @property
    def fleet_id(self) -> str:
        return self._fleet_id

    @property
    def public_key_b64(self) -> str:
        return self._public_key_b64

    @property
    def display_name(self) -> str:
        return self._display_name

    def sign_proof(self, enrollment_token: str, workspace_id: str) -> str:
        """Sign ``<enrollment_token>:<workspace_id>`` — the base64 proof-of-possession the instance
        verifies against this identity's public key (design 21)."""
        signature = self._private_key.sign(proof_message(enrollment_token, workspace_id))
        return base64.b64encode(signature).decode("ascii")

    def sign_deploy(self, kind: str, artifact_id: str, content_hash: str) -> str:
        """Sign ``deploy:<kind>:<id>:<content_hash>`` — the base64 signature the instance verifies
        against the pinned key before applying a deploy (design 22)."""
        signature = self._private_key.sign(deploy_message(kind, artifact_id, content_hash))
        return base64.b64encode(signature).decode("ascii")

    def public_dict(self) -> dict[str, str]:
        """The non-secret identity — safe to expose (never the private key)."""
        return {
            "fleet_id": self._fleet_id,
            "fleet_public_key": self._public_key_b64,
            "display_name": self._display_name,
        }


__all__ = [
    "FLEET_ID_PREFIX",
    "FleetIdentity",
    "deploy_message",
    "fleet_id_from_public_key",
    "proof_message",
]
