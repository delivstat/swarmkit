"""CredentialStore — the panel's encrypted store of per-instance membership credentials (design 19).

Holds the membership API key the panel received from an instance's ``/fleet/register`` — the secret
it uses to make future authenticated ``/fleet/*`` calls. The secret is **encrypted at rest** via a
:class:`SecretBox` (never plaintext, never a file); the DB row holds only ciphertext + metadata.
Callers pass/receive plaintext; encryption is transparent. SQLAlchemy Core over SQLite or Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from swarmkit_control_plane._secret_box import SecretBox, default_secret_box
from swarmkit_control_plane._store_base import Store, upsert
from swarmkit_control_plane._tables import instance_credential


class CredentialStore(Store):
    """Encrypted per-instance membership-credential store."""

    def __init__(self, backing: Any, secret_box: SecretBox | None = None) -> None:
        super().__init__(backing)
        self._box = secret_box or default_secret_box()

    def put_credential(
        self,
        instance_id: str,
        *,
        membership_id: str,
        fleet_id: str,
        scope: str,
        fingerprint: str,
        secret: str,
    ) -> None:
        """Store (replace) an instance's membership credential — the secret is encrypted here."""
        with self._lock, self._engine.begin() as conn:
            values = {
                "instance_id": instance_id,
                "membership_id": membership_id,
                "fleet_id": fleet_id,
                "scope": scope,
                "fingerprint": fingerprint,
                "ciphertext": self._box.encrypt(secret),
                "created_at": datetime.now(UTC).isoformat(),
            }
            conn.execute(
                upsert(
                    self._engine,
                    instance_credential,
                    values,
                    index_elements=["instance_id"],
                    set_={k: values[k] for k in values if k != "instance_id"},
                )
            )

    def get_metadata(self, instance_id: str) -> dict[str, Any] | None:
        """Return membership metadata (no secret) for display/audit, or None."""
        c = instance_credential.c
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(c.membership_id, c.fleet_id, c.scope, c.fingerprint, c.created_at).where(
                        c.instance_id == instance_id
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def delete(self, instance_id: str) -> bool:
        """Forget an instance's stored membership credential (after leaving the fleet). Returns True
        if a row was removed."""
        with self._lock, self._engine.begin() as conn:
            result = conn.execute(
                instance_credential.delete().where(instance_credential.c.instance_id == instance_id)
            )
        return bool(result.rowcount)

    def get_secret(self, instance_id: str) -> str | None:
        """Decrypt + return the membership secret for authenticating a call, or None if absent."""
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(instance_credential.c.ciphertext).where(
                        instance_credential.c.instance_id == instance_id
                    )
                )
                .mappings()
                .first()
            )
        return self._box.decrypt(row["ciphertext"]) if row else None
