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

from swarmkit_runtime._principal import current_principal
from swarmkit_runtime.oauth._refresh import ConsentRequired

logger = logging.getLogger("swarmkit.credentials")

__all__ = [
    "GLOBAL",
    "PER_USER",
    "ConsentRequired",
    "CredentialError",
    "CredentialService",
    "identity_of",
    "supports_per_user",
]

_CLOUD_SOURCES = frozenset(
    {"hashicorp-vault", "aws-secrets-manager", "gcp-secret-manager", "azure-key-vault", "plugin"}
)

#: The two kinds of connection (`per-caller-credential-delegation.md`). `global` is the default
#: because `serve` may run with `auth: none`, the CLI has no caller and a trigger has no human —
#: a per-user default would make the common deployment un-runnable. The direction that needs a
#: deliberate act is the one with an identity in it.
GLOBAL = "global"
PER_USER = "per-user"

#: Sources whose secret can be keyed by owner. `env` is process-wide and `file` is the same bytes
#: for everyone, so neither can be per-user however the workspace is written. This is a property of
#: the provider rather than a hard-coded list at the call site, so a future vault provider with a
#: per-owner path template becomes per-user capable without touching the resolver.
_PER_USER_SOURCES = frozenset({"oauth"})


def supports_per_user(source: str) -> bool:
    """Whether a secret from this source can be keyed by owner."""
    return source in _PER_USER_SOURCES


def identity_of(entry: dict[str, Any]) -> str:
    """The connection kind an entry declares, defaulting to `global` when it declares none."""
    return str(entry.get("identity") or GLOBAL)


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
        identity = identity_of(entry)

        if identity == PER_USER and not supports_per_user(source):
            # Normally caught by `swarmkit validate` at load; repeated here because resolution is
            # reachable from entry points that never loaded a workspace through the validator, and
            # silently treating this as `global` would share one secret among every caller.
            msg = (
                f"credential {name!r} is `identity: per-user`, which source {source!r} cannot "
                f"honour — its secret is the same bytes for every caller. Use `oauth`, or declare "
                f"this credential `identity: global`."
            )
            raise CredentialError(msg)

        if source == "env":
            return self._require(name, os.environ.get(str(config.get("env", ""))), source)
        if source == "file":
            return self._require(name, _read_file(str(config.get("path", ""))), source)
        if source == "oauth":
            return await self._resolve_oauth(name, config, identity)
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

    async def _resolve_oauth(
        self, name: str, config: dict[str, Any], identity: str = GLOBAL
    ) -> str:
        """A valid access token, refreshed first if it would expire inside the run window."""
        from swarmkit_runtime.oauth._refresh import (  # noqa: PLC0415
            _expires_within,
            refresh_credential,
            run_window_s,
        )

        owner = self._owner_for(name, config, identity)
        meta = self.token_store.metadata(name, owner) if owner else None
        if meta is None:
            raise CredentialError(self._no_token_message(name, owner, identity))

        if _expires_within(meta, run_window_s()):
            async with httpx.AsyncClient(timeout=20.0) as client:
                await refresh_credential(self.token_store, name, owner, client=client)
                logger.info("credential %r refreshed at the point of use", name)

        token = self.token_store.access_token(name, owner)
        if not token:
            msg = f"credential {name!r} resolved to an empty token after refresh."
            raise CredentialError(msg)
        return str(token)

    def _owner_for(self, name: str, config: dict[str, Any], identity: str) -> str:
        """Whose token this resolution may use.

        The one place the two connection kinds differ, and the only place the security property of
        `per-caller-credential-delegation.md` is enforced:

        * `global` keeps exactly the behaviour that shipped — a literal owner, else the sole stored
          owner when a workspace has only one. Being shared is what the operator declared.
        * `per-user` is the authenticated caller or nothing. It never consults a literal owner (the
          schema refuses that pair anyway), never takes the sole-owner shortcut, and never borrows
          a global connection's designated owner. Delegation only ever narrows.
        """
        if identity != PER_USER:
            return str(config.get("owner", "")) or _sole_owner(self.token_store, name)

        principal = current_principal()
        if not principal:
            msg = (
                f"credential {name!r} is `identity: per-user`, so it resolves to the token of the "
                f"caller who started this run — and this run has no authenticated caller. A CLI "
                f"run, a cron trigger and an unauthenticated server all reach here. Use an "
                f"`identity: global` credential with a designated owner for unattended work."
            )
            raise CredentialError(msg)
        return principal

    def _no_token_message(self, name: str, owner: str, identity: str) -> str:
        """Why there is no token — distinguishing the two causes that look identical to a user.

        A per-user miss is either a genuine first run, or the failure this design fears most: the
        login recorded the owner under one identifier (an opaque `sub`) and the API call presents
        another (`email`, `upn`), so the person connects successfully, every run reports no token,
        and the portal offers Connect again. Forever.

        The two get different messages. The mismatch one deliberately does **not** name the other
        owners: the caller learns their own principal and that a mismatch is likely, and the
        specifics go to the operator's log, because disclosing who else has connected is exactly
        the roster leak the per-caller read is designed to prevent.
        """
        if identity != PER_USER:
            return (
                f"credential {name!r} has no stored token for {owner or 'any owner'}. "
                f"Connect it on the Connections page, or with `swarmkit connect`."
            )

        others = sum(1 for m in self.token_store.list_metadata() if m.credential_id == name)
        if not others:
            return (
                f"credential {name!r} has no stored token for you ({owner}). Connect your own "
                f"account on the Connections page, or with `swarmkit connect`."
            )

        logger.warning(
            "per-user credential %r: no token for principal %r, but %d stored login(s) exist for "
            "this credential under other identifiers — the identity recorded at login probably "
            "differs from the claim presented on API calls (see identity_claim in server.auth)",
            name,
            owner,
            others,
        )
        return (
            f"credential {name!r} has no stored token for you ({owner}), though this connection "
            f"has stored logins under other identifiers. If you have already connected it, the "
            f"identity your login recorded differs from the one your API token presents — compare "
            f"`GET /whoami` with what the Connections page shows, and ask an operator to check "
            f"`identity_claim`."
        )

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
