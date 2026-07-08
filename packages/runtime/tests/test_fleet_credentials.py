"""Fleet credential + membership store (design 19, Phase 2 slice 1).

Auth code — tested hard: enrollment tokens are single-use + TTL-bounded, secrets are never stored
(only hashes), memberships round-trip through their issued key, and revocation stops the key.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.fleet import MembershipStore, secret_hash
from swarmkit_runtime.fleet._store import _enrollment_tokens, _memberships


@pytest.fixture()
def store(tmp_path: Path) -> MembershipStore:
    return MembershipStore(tmp_path)


# ---- enrollment tokens -------------------------------------------------------


def test_enrollment_token_is_single_use(store: MembershipStore) -> None:
    tok = store.create_enrollment_token("monitor")
    assert store.consume_enrollment_token(tok) == "monitor"
    # second use is rejected — the token was spent.
    assert store.consume_enrollment_token(tok) is None


def test_enrollment_token_expires(store: MembershipStore) -> None:
    tok = store.create_enrollment_token("manage", ttl_seconds=-1)  # already expired
    assert store.consume_enrollment_token(tok) is None


def test_unknown_enrollment_token_rejected(store: MembershipStore) -> None:
    assert store.consume_enrollment_token("not-a-real-token") is None


def test_enrollment_secret_is_not_stored(store: MembershipStore) -> None:
    tok = store.create_enrollment_token("monitor")
    with store.engine.connect() as conn:
        from sqlalchemy import select  # noqa: PLC0415

        rows = conn.execute(select(_enrollment_tokens)).mappings().all()
    assert len(rows) == 1
    assert tok not in str(dict(rows[0]))  # only the hash is persisted
    assert rows[0]["token_hash"] == secret_hash(tok)


# ---- memberships -------------------------------------------------------------


def test_issue_then_authenticate_roundtrip(store: MembershipStore) -> None:
    membership, key = store.issue_membership("fleet-a", "monitor")
    assert membership.fleet_id == "fleet-a" and membership.scope == "monitor"
    resolved = store.authenticate(key)
    assert resolved is not None
    assert resolved.membership_id == membership.membership_id
    assert resolved.scope == "monitor"


def test_wrong_key_does_not_authenticate(store: MembershipStore) -> None:
    store.issue_membership("fleet-a", "manage")
    assert store.authenticate("some-other-key") is None


def test_membership_key_secret_is_not_stored(store: MembershipStore) -> None:
    _, key = store.issue_membership("fleet-a", "monitor")
    with store.engine.connect() as conn:
        from sqlalchemy import select  # noqa: PLC0415

        row = conn.execute(select(_memberships)).mappings().first()
    assert row is not None
    assert key not in str(dict(row))
    assert row["key_hash"] == secret_hash(key)


def test_revoke_stops_the_key(store: MembershipStore) -> None:
    membership, key = store.issue_membership("fleet-a", "manage")
    assert store.authenticate(key) is not None
    assert store.revoke_membership(membership.membership_id) is True
    assert store.authenticate(key) is None  # ejected — the key no longer works
    assert store.revoke_membership(membership.membership_id) is False  # already gone


def test_expired_membership_key_rejected(store: MembershipStore) -> None:
    _, key = store.issue_membership("fleet-a", "monitor", ttl_seconds=-1)
    assert store.authenticate(key) is None


def test_rotate_issues_new_key_and_invalidates_the_old(store: MembershipStore) -> None:
    membership, key = store.issue_membership("fleet-a", "manage")
    rotated = store.rotate(key)
    assert rotated is not None
    new_membership, new_key = rotated
    assert new_membership.membership_id == membership.membership_id  # same membership
    assert new_key != key
    assert store.authenticate(new_key) is not None  # new key works
    assert store.authenticate(key) is None  # old key stops working


def test_rotate_with_bad_key_returns_none(store: MembershipStore) -> None:
    assert store.rotate("not-a-key") is None


def test_multi_fleet_memberships_are_independent(store: MembershipStore) -> None:
    _, key_a = store.issue_membership("fleet-a", "monitor")
    m_b, key_b = store.issue_membership("fleet-b", "manage")
    assert {m.fleet_id for m in store.list_memberships()} == {"fleet-a", "fleet-b"}
    # revoking one fleet leaves the other working.
    store.revoke_membership(m_b.membership_id)
    assert store.authenticate(key_a) is not None
    assert store.authenticate(key_b) is None
