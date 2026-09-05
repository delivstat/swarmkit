"""CredentialService — resolve a credential reference to a secret that works right now.

The seam `workspace-schema-v1.md` specified and nobody built: `{source, config}` where `source`
selects the provider. Built-in sources are `env`, `file` and `oauth`; the cloud backends stay
unimplemented until somebody needs them, because this change is about *where* resolution happens,
not about how many backends exist.

Why a service rather than a function: `oauth` needs I/O and a store, and the whole point of
`credential-service.md` is that every entry point reaches the same instance rather than each
assembling its own.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from swarmkit_runtime.oauth._refresh import ConsentRequired

logger = logging.getLogger("swarmkit.credentials")

__all__ = ["ConsentRequired", "CredentialError", "CredentialService"]

_CLOUD_SOURCES = frozenset(
    {"hashicorp-vault", "aws-secrets-manager", "gcp-secret-manager", "azure-key-vault", "plugin"}
)


class CredentialError(RuntimeError):
    """A credential could not be resolved. Says which one, and what would fix it."""


class CredentialService:
    """Resolves workspace credential references. One instance per workspace.

    Deliberately holds no cache. The provider's expiry is the only clock that matters, and a second
    TTL here would be a second source of truth about whether a token is alive — which is how a
    system ends up confidently presenting a dead credential.
    """

    def __init__(self, workspace_path: Path, credentials: dict[str, Any] | None = None) -> None:
        self._workspace = workspace_path
        self._credentials = credentials or {}
        self._store: Any | None = None

    def with_credentials(self, credentials: dict[str, Any] | None) -> CredentialService:
        """Same workspace, a different credentials block (used when a workspace reloads)."""
        service = CredentialService(self._workspace, credentials)
        service._store = self._store
        return service

    @property
    def token_store(self) -> Any:
        """Lazy: a workspace with no OAuth credential never opens the database."""
        if self._store is None:
            from swarmkit_runtime.oauth import TokenStore  # noqa: PLC0415

            self._store = TokenStore(self._workspace)
        return self._store

    def entry(self, name: str) -> dict[str, Any]:
        entry = self._credentials.get(name)
        if not isinstance(entry, dict):
            msg = (
                f"credential {name!r} is not declared in the workspace `credentials` block. "
                f"Declared: {sorted(self._credentials) or 'none'}."
            )
            raise CredentialError(msg)
        return entry

    def is_oauth(self, name: str) -> bool:
        try:
            return str(self.entry(name).get("source", "")) == "oauth"
        except CredentialError:
            return False

    async def resolve(self, name: str) -> str:
        """The secret for this reference, valid now.

        For `oauth` that may mean refreshing before returning — which is the entire reason this is
        a service and not a lookup table.
        """
        entry = self.entry(name)
        source = str(entry.get("source", ""))
        config = entry.get("config") or {}

        if source == "env":
            return self._require(name, os.environ.get(str(config.get("env", ""))), source)
        if source == "file":
            return self._require(name, _read_file(str(config.get("path", ""))), source)
        if source == "oauth":
            return await self._resolve_oauth(name, config)
        if source in _CLOUD_SOURCES:
            msg = (
                f"credential {name!r} uses source {source!r}, which needs a SecretsProvider "
                f"that is not wired yet. Use `env` or `file`, or implement the provider."
            )
            raise CredentialError(msg)
        msg = f"credential {name!r} has unknown source {source!r}."
        raise CredentialError(msg)

    def resolve_sync(self, name: str) -> str:
        """Synchronous resolution, for callers that cannot await.

        Refuses `oauth` rather than blocking an event loop or returning a possibly-stale token:
        being unable to refresh is a different answer from having refreshed, and quietly returning
        the stored bytes would reintroduce exactly the bug this service exists to remove.
        """
        entry = self.entry(name)
        if str(entry.get("source", "")) == "oauth":
            msg = (
                f"credential {name!r} is an OAuth credential and must be resolved asynchronously, "
                f"so it can be refreshed at the point of use."
            )
            raise CredentialError(msg)
        config = entry.get("config") or {}
        source = str(entry.get("source", ""))
        if source == "env":
            return self._require(name, os.environ.get(str(config.get("env", ""))), source)
        if source == "file":
            return self._require(name, _read_file(str(config.get("path", ""))), source)
        msg = f"credential {name!r} cannot be resolved synchronously from source {source!r}."
        raise CredentialError(msg)

    async def _resolve_oauth(self, name: str, config: dict[str, Any]) -> str:
        """A valid access token, refreshed first if it would expire inside the run window."""
        from swarmkit_runtime.oauth._refresh import (  # noqa: PLC0415
            _expires_within,
            refresh_credential,
            run_window_s,
        )

        owner = str(config.get("owner", "")) or _sole_owner(self.token_store, name)
        meta = self.token_store.metadata(name, owner) if owner else None
        if meta is None:
            msg = (
                f"credential {name!r} has no stored token for {owner or 'any owner'}. "
                f"Connect it on the Connections page, or with `swarmkit connect`."
            )
            raise CredentialError(msg)

        if _expires_within(meta, run_window_s()):
            async with httpx.AsyncClient(timeout=20.0) as client:
                await refresh_credential(self.token_store, name, owner, client=client)
                logger.info("credential %r refreshed at the point of use", name)

        token = self.token_store.access_token(name, owner)
        if not token:
            msg = f"credential {name!r} resolved to an empty token after refresh."
            raise CredentialError(msg)
        return str(token)

    @staticmethod
    def _require(name: str, value: str | None, source: str) -> str:
        if not value:
            msg = (
                f"credential {name!r} (source {source!r}) resolved to nothing. Check the "
                f"environment variable or file it points at."
            )
            raise CredentialError(msg)
        return value


def _read_file(path: str) -> str | None:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _sole_owner(store: Any, credential_id: str) -> str:
    """The owner when a credential has exactly one.

    A workspace `credentials` entry may omit `owner` — the single-operator case. With more than one
    stored owner it is genuinely ambiguous, and picking arbitrarily would mean a run silently
    acting as somebody who did not start it.
    """
    owners = [str(m.owner) for m in store.list_metadata() if m.credential_id == credential_id]
    return owners[0] if len(owners) == 1 else ""
