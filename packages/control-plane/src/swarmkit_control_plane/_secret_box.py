"""SecretBox — encrypt panel-held secrets at rest (design 19, Phase 2).

The panel *receives* a membership credential from an instance's ``/fleet/register`` and must hold it
to make future authenticated calls. It is stored **encrypted in the database, never plaintext and
never a file**. Encryption is a **pluggable seam** so a production deployment can move the key out
of the panel entirely:

* ``FernetSecretBox`` (default, zero-ops) — authenticated symmetric encryption (AES-128-CBC + HMAC),
  keyed by ``SWARMKIT_CONTROL_PLANE_SECRET_KEY`` (a Fernet key). No key set → an **ephemeral** key
  (dev only; stored credentials won't survive a restart) with a warning.
* ``VaultTransitSecretBox`` (production) — encrypt/decrypt via a Vault/OpenBao **Transit** engine.
  The DB still holds only ciphertext (``vault:v1:…``), but the key never leaves the vault. Same
  interface, selected by ``SWARMKIT_CONTROL_PLANE_VAULT_*`` env, runnable as a compose service.

The ``CredentialStore`` deals in plaintext at its edges and delegates to a ``SecretBox`` — swapping
backends touches nothing else.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Protocol, runtime_checkable

import httpx
from cryptography.fernet import Fernet

logger = logging.getLogger("swarmkit.control_plane.secrets")


class SecretBoxError(RuntimeError):
    """A secret-box backend failed (e.g. the vault rejected a request)."""


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


class VaultTransitSecretBox:
    """Encryption-as-a-service via a Vault / OpenBao **Transit** engine.

    ``encrypt`` returns the vault's ``vault:v1:…`` ciphertext (which the DB stores); ``decrypt``
    sends it back. The data key never leaves the vault, so a leaked panel DB + env can't decrypt
    the stored credentials alone. Transit wants base64 plaintext, which this wraps. A ``transport``
    is injectable for testing (``httpx.MockTransport``), mirroring ``ServeClient``.
    """

    def __init__(
        self,
        *,
        addr: str,
        token: str,
        key_name: str,
        namespace: str | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"X-Vault-Token": token}
        if namespace:
            headers["X-Vault-Namespace"] = namespace
        self._key = key_name
        self._client = httpx.Client(
            base_url=addr.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def _post(self, path: str, json: dict[str, str]) -> dict[str, str]:
        try:
            resp = self._client.post(path, json=json)
        except httpx.HTTPError as exc:
            raise SecretBoxError(f"vault transit request failed: {exc}") from exc
        if resp.status_code != 200:
            raise SecretBoxError(
                f"vault transit {path} returned {resp.status_code}: {resp.text[:200]}"
            )
        data: dict[str, str] = resp.json().get("data", {})
        return data

    def encrypt(self, plaintext: str) -> str:
        b64 = base64.b64encode(plaintext.encode()).decode()
        data = self._post(f"/v1/transit/encrypt/{self._key}", {"plaintext": b64})
        ct = data.get("ciphertext")
        if not ct:
            raise SecretBoxError("vault transit encrypt returned no ciphertext")
        return ct

    def decrypt(self, token: str) -> str:
        data = self._post(f"/v1/transit/decrypt/{self._key}", {"ciphertext": token})
        b64 = data.get("plaintext")
        if b64 is None:
            raise SecretBoxError("vault transit decrypt returned no plaintext")
        return base64.b64decode(b64).decode()

    def close(self) -> None:
        self._client.close()


def default_secret_box() -> SecretBox:
    """Select the SecretBox backend from env.

    Precedence: ``SWARMKIT_CONTROL_PLANE_VAULT_ADDR`` (+ ``…_VAULT_TOKEN``, ``…_VAULT_TRANSIT_KEY``,
    optional ``…_VAULT_NAMESPACE``) → Vault Transit; else ``SWARMKIT_CONTROL_PLANE_SECRET_KEY`` → a
    persistent local key; else an ephemeral local key (dev) with a warning.
    """
    addr = os.environ.get("SWARMKIT_CONTROL_PLANE_VAULT_ADDR", "").strip()
    if addr:
        key_name = os.environ.get(
            "SWARMKIT_CONTROL_PLANE_VAULT_TRANSIT_KEY", "swarmkit-control-plane"
        ).strip()
        logger.info("Secret backend: Vault Transit (%s, key=%s)", addr, key_name)
        return VaultTransitSecretBox(
            addr=addr,
            token=os.environ.get("SWARMKIT_CONTROL_PLANE_VAULT_TOKEN", "").strip(),
            key_name=key_name,
            namespace=os.environ.get("SWARMKIT_CONTROL_PLANE_VAULT_NAMESPACE", "").strip() or None,
        )
    key = os.environ.get("SWARMKIT_CONTROL_PLANE_SECRET_KEY", "").strip()
    if key:
        return FernetSecretBox(key.encode())
    logger.warning(
        "No SWARMKIT_CONTROL_PLANE_VAULT_ADDR or …_SECRET_KEY set — using an ephemeral encryption "
        "key. Stored fleet credentials will NOT survive a panel restart. Set a Fernet key (from "
        "`cryptography.fernet.Fernet.generate_key()`) for persistence, or point at a vault."
    )
    return FernetSecretBox(Fernet.generate_key())


__all__ = [
    "FernetSecretBox",
    "SecretBox",
    "SecretBoxError",
    "VaultTransitSecretBox",
    "default_secret_box",
]
