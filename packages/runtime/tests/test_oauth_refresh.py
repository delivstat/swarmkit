"""Pre-run refresh — the layer that turns *logged in once* into *still works at 3am*.

The claim being tested is the design's central one: a run that would fail at minute eight because a
token expired at minute three is dealt with at minute zero. So the assertions are about *when* the
refresh happens and *what happens when it cannot*, not merely that an HTTP call was made.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.mcp._client import MCPServerConfig
from swarmkit_runtime.oauth import (
    KEY_ENV,
    ConsentRequired,
    TokenStore,
    expiring_soon,
    refresh_credential,
    refresh_for_run,
)
from swarmkit_runtime.oauth._refresh import DEFAULT_RUN_WINDOW_S, run_window_s

TOKEN_ENDPOINT = "https://auth.example/token"
calls: list[dict[str, str]] = []


def _provider(status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(httpx.QueryParams(request.content.decode())))
        if status >= 400:
            return httpx.Response(status, text="invalid_grant")
        return httpx.Response(200, json={"access_token": "fresh-access", "expires_in": 3600})

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _reset() -> None:
    calls.clear()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TokenStore:
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.delenv("SWARMKIT_OAUTH_RUN_WINDOW_S", raising=False)
    return TokenStore(tmp_path)


def _save(
    store: TokenStore, *, expires_in: float | None, refresh: str | None = "refresh-1"
) -> None:
    payload: dict[str, Any] = {"access_token": "old-access"}
    if expires_in is not None:
        payload["expires_in"] = expires_in
    if refresh:
        payload["refresh_token"] = refresh
    store.save(
        credential_id="linear",
        owner="srijith",
        provider="linear",
        endpoint="https://mcp.linear.app/mcp",
        token_response=payload,
        metadata={"token_endpoint": TOKEN_ENDPOINT, "client_id": "c1"},
    )


# ---- when it fires ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_token_expiring_inside_the_window_is_refreshed_before_the_run(
    store: TokenStore,
) -> None:
    _save(store, expires_in=60)  # dies well inside a 900s run window
    async with httpx.AsyncClient(transport=_provider()) as client:
        outcomes = await refresh_for_run(store, {"linear"}, client=client)
    assert [o.refreshed for o in outcomes] == [True]
    assert store.access_token("linear", "srijith") == "fresh-access"
    assert calls[-1]["grant_type"] == "refresh_token"


@pytest.mark.asyncio
async def test_a_token_outliving_the_window_is_left_alone(store: TokenStore) -> None:
    """One round trip against a run about to make many is cheap; a needless one is still waste."""
    _save(store, expires_in=DEFAULT_RUN_WINDOW_S + 3600)
    async with httpx.AsyncClient(transport=_provider()) as client:
        assert await refresh_for_run(store, {"linear"}, client=client) == []
    assert calls == []


@pytest.mark.asyncio
async def test_a_token_with_no_expiry_is_never_refreshed(store: TokenStore) -> None:
    _save(store, expires_in=None)
    async with httpx.AsyncClient(transport=_provider()) as client:
        assert await refresh_for_run(store, {"linear"}, client=client) == []


@pytest.mark.asyncio
async def test_only_the_credentials_this_run_uses_are_touched(store: TokenStore) -> None:
    """A topology that never reaches Linear should not trigger a Linear refresh."""
    _save(store, expires_in=60)
    async with httpx.AsyncClient(transport=_provider()) as client:
        assert await refresh_for_run(store, {"github"}, client=client) == []
    assert calls == []


@pytest.mark.asyncio
async def test_no_credentials_is_a_no_op(store: TokenStore) -> None:
    async with httpx.AsyncClient(transport=_provider()) as client:
        assert await refresh_for_run(store, set(), client=client) == []


def test_the_window_is_overridable_per_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open question 1 in the design: the constant is a placeholder for a per-topology p95."""
    assert run_window_s() == DEFAULT_RUN_WINDOW_S
    monkeypatch.setenv("SWARMKIT_OAUTH_RUN_WINDOW_S", "60")
    assert run_window_s() == 60.0
    monkeypatch.setenv("SWARMKIT_OAUTH_RUN_WINDOW_S", "not-a-number")
    assert run_window_s() == DEFAULT_RUN_WINDOW_S


# ---- when it cannot ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_refresh_raises_consent_required_naming_what_to_do(
    store: TokenStore,
) -> None:
    """Not retryable: the refresh token is revoked, expired, or its scope changed, and only a
    person in a browser can fix it. Starting the run anyway would do work already known to fail."""
    _save(store, expires_in=60)
    async with httpx.AsyncClient(transport=_provider(400)) as client:
        with pytest.raises(ConsentRequired, match="Reconnect it on the Connections page"):
            await refresh_for_run(store, {"linear"}, client=client)


@pytest.mark.asyncio
async def test_a_credential_with_no_refresh_token_says_so_now_not_at_the_401(
    store: TokenStore,
) -> None:
    _save(store, expires_in=60, refresh=None)
    async with httpx.AsyncClient(transport=_provider()) as client:
        with pytest.raises(ConsentRequired, match="no refresh token"):
            await refresh_for_run(store, {"linear"}, client=client)


@pytest.mark.asyncio
async def test_an_unrecorded_token_endpoint_is_consent_not_a_crash(store: TokenStore) -> None:
    store.save(
        credential_id="linear",
        owner="srijith",
        provider="linear",
        endpoint="https://mcp.linear.app/mcp",
        token_response={"access_token": "a", "refresh_token": "r", "expires_in": 60},
        metadata={},
    )
    async with httpx.AsyncClient(transport=_provider()) as client:
        with pytest.raises(ConsentRequired, match="token endpoint"):
            await refresh_credential(store, "linear", "srijith", client=client)


@pytest.mark.asyncio
async def test_refreshing_something_absent_is_not_an_error(store: TokenStore) -> None:
    async with httpx.AsyncClient(transport=_provider()) as client:
        outcome = await refresh_credential(store, "nope", "srijith", client=client)
    assert outcome.refreshed is False


@pytest.mark.asyncio
async def test_the_refresh_token_survives_a_response_that_omits_it(store: TokenStore) -> None:
    """Providers usually omit it. Discarding the stored one would force a login at the next expiry
    — which is the failure this whole layer exists to prevent."""
    _save(store, expires_in=60)
    async with httpx.AsyncClient(transport=_provider()) as client:
        await refresh_for_run(store, {"linear"}, client=client)
    assert store.refresh_token("linear", "srijith") == "refresh-1"


# ---- what should be announced ------------------------------------------------------------------


def test_a_renewable_credential_is_not_announced(store: TokenStore) -> None:
    """Crying wolf about something the runtime renews on its own is how a real expiry gets
    ignored."""
    _save(store, expires_in=10)
    assert expiring_soon(store) == []


def test_a_non_renewable_credential_is_announced_with_its_urgency(store: TokenStore) -> None:
    _save(store, expires_in=3 * 86_400, refresh=None)
    (meta, urgency) = expiring_soon(store)[0]
    assert meta.credential_id == "linear"
    assert urgency == "notice"

    _save(store, expires_in=3600, refresh=None)
    assert expiring_soon(store)[0][1] == "urgent"


def test_something_far_off_is_not_announced_yet(store: TokenStore) -> None:
    _save(store, expires_in=30 * 86_400, refresh=None)
    assert expiring_soon(store) == []


def test_a_token_with_no_expiry_is_never_announced(store: TokenStore) -> None:
    _save(store, expires_in=None, refresh=None)
    assert expiring_soon(store) == []


def test_expiry_is_measured_against_now(store: TokenStore) -> None:
    _save(store, expires_in=3600, refresh=None)
    remaining = store.metadata("linear", "srijith")
    assert remaining is not None
    assert remaining.seconds_remaining is not None
    assert 3500 < remaining.seconds_remaining <= 3600
    assert remaining.expires_at is not None
    assert remaining.expires_at > time.time()


# ---- the wiring ------------------------------------------------------------------------------
# Testing the module in isolation proves refresh works; it does not prove the runtime calls it.
# That gap is exactly how the notification providers shipped complete and unreachable.


@pytest.mark.asyncio
async def test_the_runtime_refreshes_before_starting_servers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_refresh_oauth_for` runs against the servers a topology needs, before they start."""
    monkeypatch.delenv(KEY_ENV, raising=False)
    store = TokenStore(tmp_path)
    _save(store, expires_in=60)
    store.close()

    runtime = WorkspaceRuntime.__new__(WorkspaceRuntime)
    runtime._workspace_root = tmp_path

    class _Manager:
        configs: ClassVar[dict[str, MCPServerConfig]] = {
            "linear": MCPServerConfig(
                server_id="linear",
                transport="http",
                endpoint="https://x",
                credentials_ref="linear",
            ),
            "git": MCPServerConfig(server_id="git", transport="stdio", command=["true"]),
        }

    runtime._mcp_manager = _Manager()  # type: ignore[assignment]

    seen: dict[str, Any] = {}

    async def _fake_refresh(store_: Any, ids: set[str], **kw: Any) -> list[Any]:
        seen["ids"] = ids
        return []

    monkeypatch.setattr("swarmkit_runtime.oauth._refresh.refresh_for_run", _fake_refresh)
    await runtime._refresh_oauth_for({"linear", "git"})

    # Only the server that names a credential; `git` has none to refresh.
    assert seen["ids"] == {"linear"}


@pytest.mark.asyncio
async def test_a_workspace_with_no_oauth_servers_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case. It must not construct a store or touch the network."""
    runtime = WorkspaceRuntime.__new__(WorkspaceRuntime)
    runtime._workspace_root = tmp_path

    class _Manager:
        configs: ClassVar[dict[str, MCPServerConfig]] = {
            "git": MCPServerConfig(server_id="git", transport="stdio", command=["true"])
        }

    runtime._mcp_manager = _Manager()  # type: ignore[assignment]

    def _explode(*_: Any, **__: Any) -> None:
        raise AssertionError("must not build a token store when nothing uses OAuth")

    monkeypatch.setattr("swarmkit_runtime.oauth.TokenStore", _explode)
    await runtime._refresh_oauth_for({"git"})
