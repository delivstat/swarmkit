"""Encrypting OAuth tokens at rest.

`mcp-oauth.md`: *refresh tokens are encrypted at rest and never leave the runtime.* An access token
expires on its own; a refresh token is a standing grant against somebody's account, so the bytes get
the same treatment the fleet gives a membership credential.

Deliberately a small local implementation rather than an import from `swarmkit-control-plane`: the
runtime never depends on the panel (that boundary is a contract test, not a shared module). The
shape is the same on purpose, so the Vault-transit backend that already exists there can be lifted
across without changing callers here.

**The key must persist.** An ephemeral key means every restart silently invalidates every stored
refresh token and asks the whole workspace to log in again — a failure that looks like the provider
revoking access. The fleet panel shipped exactly that bug once. So a missing key is a loud, one-time
generation into the workspace with instructions, not a shrug.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("swarmkit.oauth")

#: Set this to keep tokens readable across a rebuild, a container replacement, or a second replica.
KEY_ENV = "SWARMKIT_OAUTH_KEY"

_KEY_FILE = "oauth.key"


class SecretBoxError(RuntimeError):
    """Ciphertext could not be decrypted — wrong key, or tampered bytes."""


class SecretBox:
    """Authenticated symmetric encryption for stored tokens."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def for_workspace(cls, workspace_path: Path) -> SecretBox:
        """The workspace's box: `$SWARMKIT_OAUTH_KEY` if set, else a key file beside its state.

        The file is generated once, `chmod 600`, and never rotated automatically — rotating it
        would invalidate every stored token, which is a decision an operator makes deliberately.
        """
        env_key = os.environ.get(KEY_ENV)
        if env_key:
            return cls(env_key.encode())

        path = workspace_path / ".swarmkit" / _KEY_FILE
        if path.exists():
            return cls(path.read_bytes().strip())

        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        path.write_bytes(key)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        logger.warning(
            "Generated an OAuth encryption key at %s. Back it up, or set %s in the environment — "
            "losing it means every stored token must be obtained again by logging in.",
            path,
            KEY_ENV,
        )
        return cls(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            msg = (
                "Stored token could not be decrypted. The encryption key has changed or the row "
                f"was altered — set {KEY_ENV} to the original key, or delete the credential and "
                "log in again."
            )
            raise SecretBoxError(msg) from exc
