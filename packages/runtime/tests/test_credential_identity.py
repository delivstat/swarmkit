"""The two connection kinds, and the one row that is the whole feature.

`identity: global` is what shipped: a connection set up once, used by every run. `identity:
per-user` resolves to the token of the authenticated caller of *this* run
(`design/details/per-caller-credential-delegation.md`).

The test that matters most is `test_per_user_never_falls_back_to_the_sole_owner`. Today's
sole-owner convenience — no owner named, exactly one stored, use it — is a kindness in a
single-operator workspace and a privilege escalation in a multi-user one: a topology that works in
development would, deployed with auth on, serve every caller from the developer's token. That test
is the difference between delegation and escalation, and it fails loudly the day someone
"simplifies" the resolver by reusing the convenience.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._principal import current_principal, principal_scope
from swarmkit_runtime.credentials import CredentialError, CredentialService
from swarmkit_runtime.oauth import TokenStore

PER_USER: dict[str, Any] = {
    "source": "oauth",
    "identity": "per-user",
    "config": {"endpoint": "https://stub.example/mcp"},
}
GLOBAL: dict[str, Any] = {
    "source": "oauth",
    "config": {"endpoint": "https://stub.example/mcp"},
}


def _store_with(tmp_path: Path, *owners: str) -> TokenStore:
    store = TokenStore(tmp_path)
    for owner in owners:
        store.save(
            credential_id="cal",
            owner=owner,
            provider="stub",
            endpoint="https://stub.example/mcp",
            token_response={
                "access_token": f"token-for-{owner}",
                "refresh_token": "r",
                "expires_in": 9999,
                "scope": "calendar.read",
            },
        )
    return store


def _service(tmp_path: Path, store: TokenStore, entry: dict[str, Any]) -> CredentialService:
    service = CredentialService(tmp_path, {"cal": entry})
    service._store = store
    return service


# --- the security property ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_user_never_falls_back_to_the_sole_owner(tmp_path: Path) -> None:
    """Alice calls; Bob is the only stored owner. She gets a refusal, never Bob's token.

    This is the escalation the two modes exist to make unrepresentable. A `global` credential in
    the same state resolves to Bob — because being shared is what `global` declares.
    """
    store = _store_with(tmp_path, "bob")

    with principal_scope("alice"), pytest.raises(CredentialError) as err:
        await _service(tmp_path, store, PER_USER).resolve("cal")

    assert "token-for-bob" not in str(err.value)
    # The same state, declared global, is the shared case and still resolves.
    assert await _service(tmp_path, store, GLOBAL).resolve("cal") == "token-for-bob"


@pytest.mark.asyncio
async def test_per_user_refuses_when_there_is_no_caller(tmp_path: Path) -> None:
    """A CLI run, a cron trigger and an unauthenticated server all reach here with no principal.

    Refusing is the feature. Falling back to the sole owner would mean an unattended schedule
    quietly acting as whichever person happened to log in.
    """
    store = _store_with(tmp_path, "bob")

    assert current_principal() is None
    with pytest.raises(CredentialError, match="no authenticated caller"):
        await _service(tmp_path, store, PER_USER).resolve("cal")


@pytest.mark.asyncio
async def test_per_user_resolves_the_callers_own_token(tmp_path: Path) -> None:
    store = _store_with(tmp_path, "alice", "bob")

    with principal_scope("alice"):
        assert await _service(tmp_path, store, PER_USER).resolve("cal") == "token-for-alice"
    with principal_scope("bob"):
        assert await _service(tmp_path, store, PER_USER).resolve("cal") == "token-for-bob"


@pytest.mark.asyncio
async def test_concurrent_callers_do_not_clobber_each_other(tmp_path: Path) -> None:
    """Why the principal is a ContextVar and not a module global.

    Two jobs resolving at once in one `swarmkit serve` process must each see their own caller. A
    global here would not be a style question — it would look exactly like Alice's run using
    Bob's token under load, intermittently.
    """
    store = _store_with(tmp_path, "alice", "bob")
    service = _service(tmp_path, store, PER_USER)

    async def resolve_as(who: str) -> str:
        with principal_scope(who):
            await asyncio.sleep(0)  # force a suspension between set and use
            return await service.resolve("cal")

    resolved = await asyncio.gather(resolve_as("alice"), resolve_as("bob"))
    assert list(resolved) == ["token-for-alice", "token-for-bob"]


# --- global is unchanged ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_by_omission_is_todays_behaviour(tmp_path: Path) -> None:
    """Every workspace that exists today declares no `identity`, and must resolve as before."""
    store = _store_with(tmp_path, "bob")
    assert await _service(tmp_path, store, GLOBAL).resolve("cal") == "token-for-bob"


@pytest.mark.asyncio
async def test_global_with_a_literal_owner_still_picks_that_owner(tmp_path: Path) -> None:
    store = _store_with(tmp_path, "alice", "bob")
    entry = {"source": "oauth", "config": {"endpoint": "https://x", "owner": "alice"}}
    # A principal is present and is *not* the designated owner: global ignores it by design.
    with principal_scope("bob"):
        assert await _service(tmp_path, store, entry).resolve("cal") == "token-for-alice"


# --- sources that cannot key by owner -------------------------------------------------------


@pytest.mark.asyncio
async def test_per_user_is_refused_on_a_source_that_cannot_key_by_owner(tmp_path: Path) -> None:
    """`env` is process-wide; there is no per-owner answer to give, so silence is not an option."""
    service = CredentialService(
        tmp_path, {"e": {"source": "env", "identity": "per-user", "config": {"env": "X"}}}
    )
    with principal_scope("alice"), pytest.raises(CredentialError, match="cannot honour"):
        await service.resolve("e")


# --- the silent failure this design fears most ----------------------------------------------


@pytest.mark.asyncio
async def test_a_first_run_and_an_identity_mismatch_say_different_things(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two causes that look identical to a user must not produce one message.

    An IdP that records an opaque `sub` at consent and presents `email` on API calls puts a user in
    an infinite loop: connect succeeds, the run reports no token, the portal offers Connect again.
    The two causes are distinguishable, and only the operator's log names the other identities —
    telling the caller who else has connected is the roster leak the per-caller read prevents.
    """
    # Nobody has connected at all: a genuine first run.
    empty = TokenStore(tmp_path / "empty")
    with principal_scope("alice"), pytest.raises(CredentialError, match="no stored token for you"):
        await _service(tmp_path / "empty", empty, PER_USER).resolve("cal")

    # Somebody has, under a different identifier.
    store = _store_with(tmp_path, "auth0|9f3c")
    with (
        principal_scope("alice@example.com"),
        caplog.at_level("WARNING", logger="swarmkit.credentials"),
        pytest.raises(CredentialError) as err,
    ):
        await _service(tmp_path, store, PER_USER).resolve("cal")

    message = str(err.value)
    assert "under other identifiers" in message
    assert "whoami" in message.lower()
    # The caller learns their own principal and nobody else's.
    assert "alice@example.com" in message
    assert "auth0|9f3c" not in message
    # The operator's log carries what the caller must not be told.
    assert "auth0|9f3c" not in caplog.text  # still not the owner string itself...
    assert "stored login(s) exist" in caplog.text  # ...but the fact, and the count.
