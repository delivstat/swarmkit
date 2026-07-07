"""SecretBox — encrypt panel-held secrets at rest (design 19, Phase 2).

The panel *receives* a membership credential from an instance's ``/fleet/register`` and must hold it
to make future authenticated calls. It is stored **encrypted in the database, never plaintext and
never a file**. Encryption is a **pluggable seam** so a production deployment can move the key out
of the panel entirely:

* ``FernetSecretBox`` (default, zero-ops) — authenticated symmetric encryption (AES-128-CBC + HMAC),
  keyed by ``SWARMKIT_CONTROL_PLANE_SECRET_KEY`` (a Fernet key). No key set → an **ephemeral** key
  (dev only; stored credentials won't survive a restart) with a warning.
* ``VaultTransitSecretBox`` (production, a later slice) — encrypt/decrypt via a Vault/OpenBao
  **Transit** engine. The DB still holds only ciphertext, but the key never leaves the vault. It
  implements this same interface, selected by ``SWARMKIT_CONTROL_PLANE_VAULT_*`` config, and can run
  as a compose service.

The ``CredentialStore`` deals in plaintext at its edges and delegates to a ``SecretBox`` — swapping
backends touches nothing else.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from cryptography.fernet import Fernet

logger = logging.getLogger("swarmkit.control_plane.secrets")


@runtime_checkable
class SecretBox(Protocol):
    """Symmetric encryption seam. ``encrypt`` returns opaque ciphertext text; ``decrypt`` inverts it
    and raises on tampering / wrong key."""

    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, token: str) -> str: ...


class FernetSecretBox:
    """Local authenticated symmetric encryption (the default backend)."""

    def __init__(self, key: bytes) -> None:
        self._f = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._f.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._f.decrypt(token.encode()).decode()


def default_secret_box() -> SecretBox:
    """Select the SecretBox backend from env.

    ``SWARMKIT_CONTROL_PLANE_SECRET_KEY`` → a persistent local key. Unset → an ephemeral key (dev)
    with a warning. (A future ``VaultTransitSecretBox`` will be selected here when the vault env is
    configured.)
    """
    key = os.environ.get("SWARMKIT_CONTROL_PLANE_SECRET_KEY", "").strip()
    if key:
        return FernetSecretBox(key.encode())
    logger.warning(
        "SWARMKIT_CONTROL_PLANE_SECRET_KEY not set — using an ephemeral encryption key. Stored "
        "fleet credentials will NOT survive a panel restart. Set the env var (a Fernet key from "
        "`cryptography.fernet.Fernet.generate_key()`) for persistence, or a vault backend."
    )
    return FernetSecretBox(Fernet.generate_key())


__all__ = ["FernetSecretBox", "SecretBox", "default_secret_box"]
