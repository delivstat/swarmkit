"""Where OAuth tokens live.

Three rules from `mcp-oauth.md`, and each is enforced by shape rather than by discipline:

* **Refresh tokens are encrypted at rest and never leave the runtime.** `metadata()` is the only
  method any HTTP route may call, and it cannot return bytes because it does not read them. Getting
  the plaintext requires `access_token()` / `refresh_token()`, which exist for the refresh loop and
  the MCP client.
* **Credentials are per-owner.** The owner is part of the primary key, so one person's login cannot
  become everyone's by accident.
* **Deletion revokes upstream where the provider supports it.** A deletion that leaves a live grant
  on someone else's server is not a deletion.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from swarmkit_runtime._sqlite import bootstrap, wal_connection
from swarmkit_runtime.oauth._secret_box import SecretBox

logger = logging.getLogger("swarmkit.oauth")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS oauth_tokens (
    credential_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    access_ciphertext TEXT NOT NULL,
    refresh_ciphertext TEXT,
    expires_at REAL,
    scopes TEXT,
    metadata_json TEXT,
    created_at REAL NOT NULL,
    refreshed_at REAL,
    PRIMARY KEY (credential_id, owner)
)
"""

#: Treat a token expiring within this window as already expired. A token with forty seconds left
#: passes a naive check and dies mid-call; the pre-run refresh exists precisely to avoid that.
EXPIRY_SKEW_S = 120.0


@dataclass(frozen=True)
class TokenMetadata:
    """Everything a route may say about a stored token. Deliberately contains no token."""

    credential_id: str
    owner: str
    provider: str
    endpoint: str
    expires_at: float | None
    scopes: list[str]
    created_at: float
    refreshed_at: float | None
    has_refresh_token: bool

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= time.time() + EXPIRY_SKEW_S

    @property
    def seconds_remaining(self) -> float | None:
        return None if self.expires_at is None else self.expires_at - time.time()


class TokenStore:
    """Encrypted OAuth token storage, one row per (credential, owner)."""

    def __init__(self, workspace_path: Path, *, box: SecretBox | None = None) -> None:
        self._path = workspace_path / ".swarmkit" / "state" / "oauth.db"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._box = box or SecretBox.for_workspace(workspace_path)
        self._conn = wal_connection(self._path)
        bootstrap(self._conn, _CREATE_TABLE)

    # ---- writing ---------------------------------------------------------------------

    def save(
        self,
        *,
        credential_id: str,
        owner: str,
        provider: str,
        endpoint: str,
        token_response: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> TokenMetadata:
        """Store a provider's token response.

        A refresh response often omits `refresh_token`, meaning *keep using the one you have*.
        Writing NULL there would discard a standing grant and force a fresh login on the next
        expiry — so an absent refresh token preserves the stored one rather than clearing it.
        """
        import json  # noqa: PLC0415

        access = str(token_response.get("access_token", ""))
        if not access:
            msg = "Token response carried no access_token"
            raise ValueError(msg)
        expires_in = token_response.get("expires_in")
        expires_at = time.time() + float(expires_in) if expires_in else None
        scopes = str(token_response.get("scope", ""))

        new_refresh = token_response.get("refresh_token")
        refresh_cipher = (
            self._box.encrypt(str(new_refresh))
            if new_refresh
            else self._existing_refresh_cipher(credential_id, owner)
        )

        now = time.time()
        existing = self._row(credential_id, owner)
        self._conn.execute(
            """
            INSERT INTO oauth_tokens (credential_id, owner, provider, endpoint,
                access_ciphertext, refresh_ciphertext, expires_at, scopes, metadata_json,
                created_at, refreshed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(credential_id, owner) DO UPDATE SET
                provider=excluded.provider, endpoint=excluded.endpoint,
                access_ciphertext=excluded.access_ciphertext,
                refresh_ciphertext=excluded.refresh_ciphertext,
                expires_at=excluded.expires_at, scopes=excluded.scopes,
                metadata_json=excluded.metadata_json, refreshed_at=excluded.refreshed_at
            """,
            (
                credential_id,
                owner,
                provider,
                endpoint,
                self._box.encrypt(access),
                refresh_cipher,
                expires_at,
                scopes,
                json.dumps(metadata or {}),
                existing[9] if existing else now,
                now if existing else None,
            ),
        )
        self._conn.commit()
        saved = self.metadata(credential_id, owner)
        assert saved is not None
        return saved

    # ---- reading ---------------------------------------------------------------------

    def metadata(self, credential_id: str, owner: str) -> TokenMetadata | None:
        """The only accessor an HTTP route may use. Cannot leak a token: it never reads one."""
        row = self._row(credential_id, owner)
        return None if row is None else self._to_metadata(row)

    def list_metadata(self) -> list[TokenMetadata]:
        rows = self._conn.execute(
            "SELECT * FROM oauth_tokens ORDER BY credential_id, owner"
        ).fetchall()
        return [self._to_metadata(r) for r in rows]

    def access_token(self, credential_id: str, owner: str) -> str | None:
        """Plaintext access token, for the MCP client. Not exposed over HTTP."""
        row = self._row(credential_id, owner)
        return None if row is None else self._box.decrypt(row[4])

    def refresh_token(self, credential_id: str, owner: str) -> str | None:
        """Plaintext refresh token, for the refresh loop only.

        Not reachable from any route — including by its owner. A token you can read is a token
        anything that can read as you can exfiltrate.
        """
        row = self._row(credential_id, owner)
        if row is None or row[5] is None:
            return None
        return self._box.decrypt(row[5])

    # ---- deletion --------------------------------------------------------------------

    async def delete(
        self,
        credential_id: str,
        owner: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        """Remove locally and revoke upstream where the provider advertises revocation.

        Local removal happens even when revocation fails: leaving the row behind because someone
        else's server was unreachable would mean a delete that did nothing visible.
        """
        import json  # noqa: PLC0415

        row = self._row(credential_id, owner)
        if row is None:
            return {"deleted": False, "reason": "not found"}

        revoked = False
        detail = "provider advertises no revocation endpoint"
        meta = json.loads(row[8] or "{}")
        endpoint = meta.get("revocation_endpoint")
        if endpoint and client is not None:
            token = self._box.decrypt(row[5]) if row[5] else self._box.decrypt(row[4])
            try:
                resp = await client.post(
                    endpoint,
                    data={"token": token, "client_id": meta.get("client_id", "")},
                )
                revoked = resp.status_code < 400
                detail = "revoked upstream" if revoked else f"provider returned {resp.status_code}"
            except httpx.HTTPError as exc:
                detail = f"revocation call failed: {type(exc).__name__}"

        self._conn.execute(
            "DELETE FROM oauth_tokens WHERE credential_id = ? AND owner = ?",
            (credential_id, owner),
        )
        self._conn.commit()
        return {"deleted": True, "revoked_upstream": revoked, "detail": detail}

    # ---- internals -------------------------------------------------------------------

    def _row(self, credential_id: str, owner: str) -> tuple[Any, ...] | None:
        cur = self._conn.execute(
            "SELECT * FROM oauth_tokens WHERE credential_id = ? AND owner = ?",
            (credential_id, owner),
        )
        row: tuple[Any, ...] | None = cur.fetchone()
        return row

    def _existing_refresh_cipher(self, credential_id: str, owner: str) -> str | None:
        row = self._row(credential_id, owner)
        return None if row is None else row[5]

    @staticmethod
    def _to_metadata(row: tuple[Any, ...]) -> TokenMetadata:
        return TokenMetadata(
            credential_id=row[0],
            owner=row[1],
            provider=row[2],
            endpoint=row[3],
            expires_at=row[6],
            scopes=[s for s in (row[7] or "").split(" ") if s],
            created_at=row[9],
            refreshed_at=row[10],
            has_refresh_token=row[5] is not None,
        )

    def close(self) -> None:
        self._conn.close()
