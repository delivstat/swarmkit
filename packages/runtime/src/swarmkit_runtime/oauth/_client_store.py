"""Persistent OAuth-client registration — the client_id + client_secret the runtime uses to
drive the OAuth dance when a provider does not support dynamic client registration (DCR).

See `design/details/oauth-persistent-clients.md`. One row per **authorization server (issuer)**
rather than per MCP endpoint, so one GCP OAuth client covers every Google API in the workspace
(Gmail + Calendar + Drive + …), not just the one its row was keyed to. See the "Update:
issuer-keyed" section of the design note (and the discussion in #1003) for the reasoning.

Discipline, matching `_store.py`:

* **The client_secret never leaves the runtime over HTTP.** `list_display` returns everything
  a route may show (display name, type, creation time); the full record — including the
  decrypted secret — is reachable only by callers inside the runtime (the login flow).
* **One client per issuer.** The primary key is the authorization-server URL. A new
  registration replaces the existing one.
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

#: v2 schema file. The v1 file (`oauth_clients.db`) was endpoint-keyed and shipped in #1002;
#: this release moves to issuer-keying (#1003). The old file is harmless to leave on disk —
#: nothing new reads it — but a fresh install will not create it either.
_DB_FILE = "oauth_clients_v2.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    issuer TEXT PRIMARY KEY,
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

    issuer: str
    client_id: str
    client_secret: str | None
    client_type: str
    display_name: str
    scopes: list[str]
    created_at: float


@dataclass(frozen=True)
class ClientDisplay:
    """A client row scrubbed for a route reader. No secret."""

    issuer: str
    client_id: str
    client_type: str
    display_name: str
    scopes: list[str]
    created_at: float


class ClientStore:
    """Encrypted persistent OAuth-client store, one row per authorization server (issuer)."""

    def __init__(self, workspace_path: Path, *, box: SecretBox | None = None) -> None:
        self._path = workspace_path / ".swarmkit" / "state" / _DB_FILE
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._box = box or SecretBox.for_workspace(workspace_path)
        self._conn = wal_connection(self._path, check_same_thread=False)
        bootstrap(self._conn, _CREATE_TABLE)

    # ---- writing ---------------------------------------------------------------------

    def save(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str | None,
        client_type: str,
        display_name: str,
        scopes: list[str] | None = None,
    ) -> ClientDisplay:
        """Store an OAuth client registration. Replaces any existing row for the same issuer."""
        if client_type not in CLIENT_TYPES:
            msg = f"client_type must be one of {CLIENT_TYPES}, got {client_type!r}"
            raise ValueError(msg)
        if not issuer or not client_id or not display_name:
            msg = "issuer, client_id and display_name are required"
            raise ValueError(msg)

        secret_cipher = self._box.encrypt(client_secret) if client_secret else None
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO oauth_clients
                (issuer, client_id, client_secret_ciphertext, client_type, display_name,
                 scopes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(issuer) DO UPDATE SET
                client_id=excluded.client_id,
                client_secret_ciphertext=excluded.client_secret_ciphertext,
                client_type=excluded.client_type,
                display_name=excluded.display_name,
                scopes=excluded.scopes
            """,
            (
                issuer,
                client_id,
                secret_cipher,
                client_type,
                display_name,
                " ".join(scopes or []),
                now,
            ),
        )
        self._conn.commit()
        return self._display(self._row(issuer))  # type: ignore[arg-type]

    # ---- reading ---------------------------------------------------------------------

    def get(self, issuer: str) -> ClientRecord | None:
        """Full record including the decrypted secret. Internal use only."""
        row = self._row(issuer)
        if row is None:
            return None
        secret = self._box.decrypt(row[2]) if row[2] else None
        return ClientRecord(
            issuer=row[0],
            client_id=row[1],
            client_secret=secret,
            client_type=row[3],
            display_name=row[4],
            scopes=[s for s in (row[5] or "").split(" ") if s],
            created_at=row[6],
        )

    def list_display(self) -> list[ClientDisplay]:
        rows = self._conn.execute("SELECT * FROM oauth_clients ORDER BY issuer").fetchall()
        return [self._display(r) for r in rows]

    # ---- deletion --------------------------------------------------------------------

    def delete(self, issuer: str) -> bool:
        cur = self._conn.execute("DELETE FROM oauth_clients WHERE issuer = ?", (issuer,))
        self._conn.commit()
        return cur.rowcount > 0

    # ---- internals -------------------------------------------------------------------

    def _row(self, issuer: str) -> tuple[Any, ...] | None:
        cur = self._conn.execute("SELECT * FROM oauth_clients WHERE issuer = ?", (issuer,))
        row: tuple[Any, ...] | None = cur.fetchone()
        return row

    @staticmethod
    def _display(row: tuple[Any, ...]) -> ClientDisplay:
        return ClientDisplay(
            issuer=row[0],
            client_id=row[1],
            client_type=row[3],
            display_name=row[4],
            scopes=[s for s in (row[5] or "").split(" ") if s],
            created_at=row[6],
        )

    def close(self) -> None:
        self._conn.close()
