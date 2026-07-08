"""MembershipStore — the instance's record of the fleets it belongs to (design 19, Phase 2).

Persists one-time **enrollment tokens** (bootstrap secrets that authorise a join) and the
**memberships** they produce (a per-fleet scoped API key the fleet uses to call this instance). Only
hashes are stored — the secrets are returned once at mint time. SQLAlchemy Core over SQLite
(default, ``.swarmkit/fleet.sqlite``) or Postgres, mirroring the runtime persistence store.

Enrollment tokens are **single-use** (``consume`` marks them spent atomically) and TTL-bounded, so a
leaked join code can't be replayed. Both credential kinds can be revoked (delete the row) — the
instance can eject a fleet unilaterally, which is what makes "maintained outside the fleet" and
multi-fleet coherent.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    Column,
    Engine,
    MetaData,
    Table,
    Text,
    delete,
    insert,
    select,
    update,
)

from swarmkit_runtime.fleet._credentials import (
    Membership,
    Scope,
    fingerprint,
    mint_secret,
    secret_hash,
)
from swarmkit_runtime.persistence._store import make_engine

_metadata = MetaData()

_enrollment_tokens = Table(
    "fleet_enrollment_tokens",
    _metadata,
    Column("token_hash", Text, primary_key=True),
    Column("scope", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("used_at", Text),
)

_memberships = Table(
    "fleet_memberships",
    _metadata,
    Column("membership_id", Text, primary_key=True),
    Column("fleet_id", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("key_hash", Text, nullable=False, unique=True),
    Column("key_fingerprint", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("expires_at", Text),
)

# Pinned fleet public keys (design 21). One row per fleet_id the instance has seen; the (non-secret)
# Ed25519 public key is pinned trust-on-first-use, so a later same-fleet_id/different-key register
# is a detectable mismatch. Separate from memberships: an instance may hold several memberships for
# one fleet, but one pinned identity.
_fleet_identities = Table(
    "fleet_identities",
    _metadata,
    Column("fleet_id", Text, primary_key=True),
    Column("public_key", Text, nullable=False),  # base64 Ed25519 public key
    Column("display_name", Text, nullable=False, default=""),
    Column("pinned_at", Text, nullable=False),
)

DEFAULT_ENROLLMENT_TTL_S = 900  # 15 minutes


def _now() -> datetime:
    return datetime.now(UTC)


class MembershipStore:
    """Instance-side store of enrollment tokens + fleet memberships."""

    def __init__(self, backing: Path | str | Engine) -> None:
        if isinstance(backing, Engine):
            self._engine = backing
        elif isinstance(backing, Path):
            db = backing / ".swarmkit" / "fleet.sqlite"
            db.parent.mkdir(parents=True, exist_ok=True)
            self._engine = make_engine(f"sqlite:///{db}")
        else:
            self._engine = make_engine(backing)
        self._lock = threading.Lock()
        _metadata.create_all(self._engine)

    @property
    def engine(self) -> Engine:
        return self._engine

    # ---- enrollment tokens ---------------------------------------------------

    def create_enrollment_token(
        self, scope: Scope, ttl_seconds: int = DEFAULT_ENROLLMENT_TTL_S
    ) -> str:
        """Mint a one-time, TTL-bounded enrollment token. Returns the secret (shown once)."""
        secret = mint_secret()
        now = _now()
        with self._lock, self._engine.begin() as conn:
            conn.execute(
                insert(_enrollment_tokens).values(
                    token_hash=secret_hash(secret),
                    scope=scope,
                    created_at=now.isoformat(),
                    expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
                )
            )
        return secret

    def consume_enrollment_token(self, secret: str) -> Scope | None:
        """Validate + spend an enrollment token (single-use). Returns its scope, or None if the
        token is unknown, already used, or expired."""
        h = secret_hash(secret)
        now = _now()
        with self._lock, self._engine.begin() as conn:
            row = (
                conn.execute(
                    select(
                        _enrollment_tokens.c.scope,
                        _enrollment_tokens.c.expires_at,
                        _enrollment_tokens.c.used_at,
                    ).where(_enrollment_tokens.c.token_hash == h)
                )
                .mappings()
                .first()
            )
            if row is None or row["used_at"] is not None:
                return None
            if datetime.fromisoformat(row["expires_at"]) <= now:
                return None
            conn.execute(
                update(_enrollment_tokens)
                .where(_enrollment_tokens.c.token_hash == h)
                .values(used_at=now.isoformat())
            )
        scope: Scope = row["scope"]
        return scope

    # ---- memberships ---------------------------------------------------------

    def issue_membership(
        self, fleet_id: str, scope: Scope, ttl_seconds: int | None = None
    ) -> tuple[Membership, str]:
        """Create a membership + issue its scoped API key. Returns ``(membership, key)``; the key is
        shown once (only its hash is stored)."""
        secret = mint_secret()
        now = _now()
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat() if ttl_seconds else None
        membership = Membership(
            membership_id=uuid4().hex[:12],
            fleet_id=fleet_id,
            scope=scope,
            key_fingerprint=fingerprint(secret),
            created_at=now.isoformat(),
            expires_at=expires_at,
        )
        with self._lock, self._engine.begin() as conn:
            conn.execute(
                insert(_memberships).values(
                    membership_id=membership.membership_id,
                    fleet_id=fleet_id,
                    scope=scope,
                    key_hash=secret_hash(secret),
                    key_fingerprint=membership.key_fingerprint,
                    created_at=membership.created_at,
                    expires_at=expires_at,
                )
            )
        return membership, secret

    def authenticate(self, key: str) -> Membership | None:
        """Resolve a membership API key to its Membership, or None if unknown/expired."""
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(_memberships).where(_memberships.c.key_hash == secret_hash(key))
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) <= _now():
            return None
        return _row_to_membership(row)

    def rotate(self, current_key: str) -> tuple[Membership, str] | None:
        """Rotate a membership's key: validate the current key, issue a new one for the **same**
        membership (the old key stops working). Returns ``(membership, new_key)``, or None if the
        current key is unknown/expired."""
        m = self.authenticate(current_key)
        if m is None:
            return None
        new_secret = mint_secret()
        with self._lock, self._engine.begin() as conn:
            conn.execute(
                update(_memberships)
                .where(_memberships.c.membership_id == m.membership_id)
                .values(key_hash=secret_hash(new_secret), key_fingerprint=fingerprint(new_secret))
            )
        rotated = Membership(
            membership_id=m.membership_id,
            fleet_id=m.fleet_id,
            scope=m.scope,
            key_fingerprint=fingerprint(new_secret),
            created_at=m.created_at,
            expires_at=m.expires_at,
        )
        return rotated, new_secret

    def list_memberships(self) -> list[Membership]:
        with self._lock, self._engine.connect() as conn:
            rows = (
                conn.execute(select(_memberships).order_by(_memberships.c.created_at))
                .mappings()
                .all()
            )
        return [_row_to_membership(r) for r in rows]

    def revoke_membership(self, membership_id: str) -> bool:
        """Eject a fleet — delete its membership (its key stops authenticating). Returns True if one
        was removed."""
        with self._lock, self._engine.begin() as conn:
            result = conn.execute(
                delete(_memberships).where(_memberships.c.membership_id == membership_id)
            )
        return result.rowcount > 0

    # ---- pinned fleet identities (design 21) --------------------------------

    def pin_fleet_key(self, fleet_id: str, public_key_b64: str, display_name: str = "") -> str:
        """Trust-on-first-use pin of a fleet's public key. Returns:

        * ``"pinned"`` — first time this ``fleet_id`` is seen; the key is recorded.
        * ``"match"``  — already pinned to the *same* key (a benign re-register).
        * ``"mismatch"`` — already pinned to a *different* key (the register must be rejected).
        """
        with self._lock, self._engine.begin() as conn:
            row = (
                conn.execute(
                    select(_fleet_identities.c.public_key).where(
                        _fleet_identities.c.fleet_id == fleet_id
                    )
                )
                .mappings()
                .first()
            )
            if row is not None:
                return "match" if row["public_key"] == public_key_b64 else "mismatch"
            conn.execute(
                insert(_fleet_identities).values(
                    fleet_id=fleet_id,
                    public_key=public_key_b64,
                    display_name=display_name,
                    pinned_at=_now().isoformat(),
                )
            )
        return "pinned"

    def get_fleet_key(self, fleet_id: str) -> str | None:
        """The pinned base64 public key for a fleet, or None if never pinned."""
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(_fleet_identities.c.public_key).where(
                        _fleet_identities.c.fleet_id == fleet_id
                    )
                )
                .mappings()
                .first()
            )
        return row["public_key"] if row else None

    def unpin_fleet_key(self, fleet_id: str) -> bool:
        """Forget a pinned fleet key (owner action) so the fleet may deliberately re-key. Returns
        True if a pin was removed."""
        with self._lock, self._engine.begin() as conn:
            result = conn.execute(
                delete(_fleet_identities).where(_fleet_identities.c.fleet_id == fleet_id)
            )
        return result.rowcount > 0


def _row_to_membership(row: object) -> Membership:
    r = dict(row)  # type: ignore[call-overload]
    return Membership(
        membership_id=r["membership_id"],
        fleet_id=r["fleet_id"],
        scope=r["scope"],
        key_fingerprint=r["key_fingerprint"],
        created_at=r["created_at"],
        expires_at=r["expires_at"],
    )
