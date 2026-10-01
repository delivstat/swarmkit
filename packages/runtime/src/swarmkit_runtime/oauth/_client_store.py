"""Persistent OAuth-client registration — the client_id + client_secret the runtime uses to
drive the OAuth dance when a provider does not support dynamic client registration (DCR).

See `design/details/oauth-persistent-clients.md`. One row per MCP server endpoint; a login
that fails DCR falls back to this store to resolve the client credentials. The secret is
encrypted at rest by the same `SecretBox` the token store uses.

Discipline, matching `_store.py`:

* **The client_secret never leaves the runtime over HTTP.** `list_display` returns everything
  a route may show (display name, type, creation time); the full record — including the
  decrypted secret — is reachable only by callers inside the runtime (the login flow).
* **One client per endpoint.** The primary key is the endpoint URL. A new registration
  replaces the existing one.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarmkit_runtime._sqlite import bootstrap, wal_connection
from swarmkit_runtime.oauth._secret_box import SecretBox

logger = logging.getLogger("swarmkit.oauth.clients")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    endpoint TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    client_secret_ciphertext TEXT,
    client_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    scopes TEXT,
    created_at REAL NOT NULL
)
"""

CLIENT_TYPES = ("desktop", "web", "dcr")


@dataclass(frozen=True)
class ClientRecord:
    """Everything the login flow needs about a registered client. Carries the decrypted
    client_secret and so is never returned over HTTP."""

    endpoint: str
    client_id: str
    client_secret: str | None
    client_type: str
    display_name: str
    scopes: list[str]
    created_at: float


@dataclass(frozen=True)
class ClientDisplay:
    """A client row scrubbed for a route reader. No secret."""

    endpoint: str
    client_id: str
    client_type: str
    display_name: str
    scopes: list[str]
    created_at: float


class ClientStore:
    """Encrypted persistent OAuth-client store, one row per MCP endpoint."""

    def __init__(self, workspace_path: Path, *, box: SecretBox | None = None) -> None:
        self._path = workspace_path / ".swarmkit" / "state" / "oauth_clients.db"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._box = box or SecretBox.for_workspace(workspace_path)
        self._conn = wal_connection(self._path, check_same_thread=False)
        bootstrap(self._conn, _CREATE_TABLE)

    # ---- writing ---------------------------------------------------------------------

    def save(
        self,
        *,
        endpoint: str,
        client_id: str,
        client_secret: str | None,
        client_type: str,
        display_name: str,
        scopes: list[str] | None = None,
    ) -> ClientDisplay:
        """Store an OAuth client registration. Replaces any existing row for the same endpoint."""
        if client_type not in CLIENT_TYPES:
            msg = f"client_type must be one of {CLIENT_TYPES}, got {client_type!r}"
            raise ValueError(msg)
        if not endpoint or not client_id or not display_name:
            msg = "endpoint, client_id and display_name are required"
            raise ValueError(msg)

        secret_cipher = self._box.encrypt(client_secret) if client_secret else None
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO oauth_clients
                (endpoint, client_id, client_secret_ciphertext, client_type, display_name,
                 scopes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                client_id=excluded.client_id,
                client_secret_ciphertext=excluded.client_secret_ciphertext,
                client_type=excluded.client_type,
                display_name=excluded.display_name,
                scopes=excluded.scopes
            """,
            (
                endpoint,
                client_id,
                secret_cipher,
                client_type,
                display_name,
                " ".join(scopes or []),
                now,
            ),
        )
        self._conn.commit()
        return self._display(self._row(endpoint))  # type: ignore[arg-type]

    # ---- reading ---------------------------------------------------------------------

    def get(self, endpoint: str) -> ClientRecord | None:
        """Full record including the decrypted secret. Internal use only."""
        row = self._row(endpoint)
        if row is None:
            return None
        secret = self._box.decrypt(row[2]) if row[2] else None
        return ClientRecord(
            endpoint=row[0],
            client_id=row[1],
            client_secret=secret,
            client_type=row[3],
            display_name=row[4],
            scopes=[s for s in (row[5] or "").split(" ") if s],
            created_at=row[6],
        )

    def list_display(self) -> list[ClientDisplay]:
        rows = self._conn.execute("SELECT * FROM oauth_clients ORDER BY endpoint").fetchall()
        return [self._display(r) for r in rows]

    # ---- deletion --------------------------------------------------------------------

    def delete(self, endpoint: str) -> bool:
        cur = self._conn.execute("DELETE FROM oauth_clients WHERE endpoint = ?", (endpoint,))
        self._conn.commit()
        return cur.rowcount > 0

    # ---- internals -------------------------------------------------------------------

    def _row(self, endpoint: str) -> tuple[Any, ...] | None:
        cur = self._conn.execute("SELECT * FROM oauth_clients WHERE endpoint = ?", (endpoint,))
        row: tuple[Any, ...] | None = cur.fetchone()
        return row

    @staticmethod
    def _display(row: tuple[Any, ...]) -> ClientDisplay:
        return ClientDisplay(
            endpoint=row[0],
            client_id=row[1],
            client_type=row[3],
            display_name=row[4],
            scopes=[s for s in (row[5] or "").split(" ") if s],
            created_at=row[6],
        )

    def close(self) -> None:
        self._conn.close()
