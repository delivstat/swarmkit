"""JoinCodeStore — the panel's one-time codes for the Mode B (instance-initiated) join.

A NAT'd instance can't be reached by the panel, so the handshake inverts: the operator mints a
**join code** in the fleet UI and hands it to the edge, which calls ``POST /fleet/join`` with it
(design 19, Mode B). This store issues those codes and spends them single-use. Only the code's hash
is persisted — the code is a high-entropy secret, so a plain SHA-256 (no salt) suffices, and nothing
reversible is stored. A code is TTL-bounded and consumed on the one successful join.

SQLAlchemy Core over SQLite (default) or Postgres — same store model as the other panel stores.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from swarmkit_control_plane._store_base import Store
from swarmkit_control_plane._tables import join_code
from swarmkit_control_plane._tokens import token_hash

DEFAULT_JOIN_TTL_S = 900  # 15 minutes — a bootstrap code, not a long-lived credential


def _now() -> datetime:
    return datetime.now(UTC)


class JoinCodeStore(Store):
    """Issue + spend one-time Mode B join codes (single-use, TTL-bounded)."""

    def mint(
        self,
        *,
        name: str = "",
        endpoint: str = "",
        tier: str = "read",
        ttl_seconds: int = DEFAULT_JOIN_TTL_S,
    ) -> str:
        """Mint a join code carrying the instance's intended name/endpoint hint + granted tier.

        Returns the plaintext code — shown **once**; only its hash is stored.
        """
        code = secrets.token_urlsafe(32)
        now = _now()
        with self._lock, self._engine.begin() as conn:
            conn.execute(
                join_code.insert().values(
                    code_hash=token_hash(code),
                    name=name,
                    endpoint=endpoint,
                    tier=tier,
                    created_at=now.isoformat(),
                    expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
                    consumed_at=None,
                )
            )
        return code

    def consume(self, code: str) -> dict[str, Any] | None:
        """Validate + spend a join code (single-use). Returns ``{name, endpoint, tier}`` on success,
        or ``None`` if the code is unknown, already consumed, or expired.

        Select-then-update under the store lock (in-process serialisation), so a code can be spent
        at most once without relying on driver ``rowcount`` (which psycopg reports as -1 for
        ``ON CONFLICT``/some updates)."""
        if not code:
            return None
        h = token_hash(code)
        now = _now()
        with self._lock, self._engine.begin() as conn:
            row = (
                conn.execute(
                    select(
                        join_code.c.name,
                        join_code.c.endpoint,
                        join_code.c.tier,
                        join_code.c.expires_at,
                        join_code.c.consumed_at,
                    ).where(join_code.c.code_hash == h)
                )
                .mappings()
                .first()
            )
            if row is None or row["consumed_at"] is not None:
                return None
            if datetime.fromisoformat(row["expires_at"]) <= now:
                return None
            conn.execute(
                update(join_code)
                .where(join_code.c.code_hash == h)
                .values(consumed_at=now.isoformat())
            )
        return {"name": row["name"], "endpoint": row["endpoint"], "tier": row["tier"]}


__all__ = ["DEFAULT_JOIN_TTL_S", "JoinCodeStore"]
