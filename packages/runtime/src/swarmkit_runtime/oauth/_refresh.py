"""Refreshing a token before a run rather than during one.

`mcp-oauth.md` argues this at length and the argument is the whole design: **a run that will fail at
minute eight because a token expired at minute three should have been dealt with at minute zero.**
Reactive refresh — call, get a 401, refresh, retry — works and is the wrong shape, for the same
reason `requires_runtime` is checked at workspace load and a command pack's `requires` before
anything runs.

Two layers live here:

* :func:`refresh_for_run` — at run start, refresh every credential the topology may use whose
  access token would expire inside the run's plausible window. Silent, one round trip, against a
  run about to make many.
* :func:`expiring_soon` — which credentials are heading for trouble, for whatever wants to announce
  it. Detection only: this module does not decide how a human hears about it.

A refresh that the provider refuses is `ConsentRequired`. That is not a retryable error — the
refresh token is revoked, expired, or its scope changed, and only a person in a browser can fix it.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx

from swarmkit_runtime.oauth._pkce import refresh_token as exchange_refresh
from swarmkit_runtime.oauth._store import TokenMetadata, TokenStore

logger = logging.getLogger("swarmkit.oauth")

#: How far ahead a run is assumed to reach. `mcp-oauth.md` open question 1: topologies vary from
#: seconds to hours, and a per-topology p95 from run history is the better answer once there is
#: history. Until then a conservative constant, overridable per deployment — being wrong in the
#: generous direction costs one unnecessary refresh, and in the mean direction costs a failed run.
DEFAULT_RUN_WINDOW_S = 900.0

#: A refresh token nearing its own end. The design's two thresholds: a week is a notification, a
#: day is a review item. Detection lives here; delivery does not.
NOTICE_WINDOW_S = 7 * 86_400.0
URGENT_WINDOW_S = 86_400.0


def run_window_s() -> float:
    raw = os.environ.get("SWARMKIT_OAUTH_RUN_WINDOW_S")
    try:
        return float(raw) if raw else DEFAULT_RUN_WINDOW_S
    except ValueError:
        return DEFAULT_RUN_WINDOW_S


class ConsentRequired(RuntimeError):
    """A refresh needs a human in a browser. Never retry this — retrying cannot fix it."""

    def __init__(self, credential_id: str, owner: str, detail: str) -> None:
        self.credential_id = credential_id
        self.owner = owner
        super().__init__(
            f"Credential {credential_id!r} ({owner}) needs a new login: {detail}. "
            f"Reconnect it on the Connections page, then start the run again."
        )


@dataclass(frozen=True)
class RefreshOutcome:
    credential_id: str
    owner: str
    refreshed: bool
    reason: str


def _expires_within(meta: TokenMetadata, window_s: float) -> bool:
    """Would this token die inside the window? A token with no expiry never does."""
    if meta.expires_at is None:
        return False
    return meta.expires_at <= time.time() + window_s


async def refresh_credential(
    store: TokenStore,
    credential_id: str,
    owner: str,
    *,
    client: httpx.AsyncClient,
) -> RefreshOutcome:
    """Exchange the stored refresh token for a new access token."""
    meta = store.metadata(credential_id, owner)
    if meta is None:
        return RefreshOutcome(credential_id, owner, False, "no stored token")

    refresh = store.refresh_token(credential_id, owner)
    if not refresh:
        # Nothing to refresh with. Only a person can fix this, and saying so now beats a 401 later.
        raise ConsentRequired(credential_id, owner, "no refresh token is stored")

    stored_meta = store.provider_config(credential_id, owner)
    token_endpoint = stored_meta.get("token_endpoint")
    if not token_endpoint:
        raise ConsentRequired(
            credential_id, owner, "the provider's token endpoint was not recorded"
        )

    try:
        tokens = await exchange_refresh(
            {"token_endpoint": token_endpoint},
            refresh=refresh,
            client_id=str(stored_meta.get("client_id", "")),
            client=client,
        )
    except PermissionError as exc:
        raise ConsentRequired(credential_id, owner, str(exc)) from exc

    store.save(
        credential_id=credential_id,
        owner=owner,
        provider=meta.provider,
        endpoint=meta.endpoint,
        token_response=tokens,
        metadata=stored_meta,
    )
    logger.info("Refreshed OAuth credential %r for %s", credential_id, owner)
    return RefreshOutcome(credential_id, owner, True, "refreshed")


async def refresh_for_run(
    store: TokenStore,
    credential_ids: set[str],
    *,
    window_s: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[RefreshOutcome]:
    """Refresh every credential this run may use that would expire inside its window.

    Raises :class:`ConsentRequired` for the first credential that cannot be refreshed silently,
    because starting the run anyway would mean doing work that is going to fail — the failure is
    already known at this point, and the useful moment to say so is now.
    """
    if not credential_ids:
        return []
    window = run_window_s() if window_s is None else window_s

    due = [
        m
        for m in store.list_metadata()
        if m.credential_id in credential_ids and _expires_within(m, window)
    ]
    if not due:
        return []

    outcomes: list[RefreshOutcome] = []
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    try:
        for meta in due:
            outcomes.append(
                await refresh_credential(store, meta.credential_id, meta.owner, client=http)
            )
    finally:
        if owns_client:
            await http.aclose()
    return outcomes


def expiring_soon(store: TokenStore) -> list[tuple[TokenMetadata, str]]:
    """Credentials heading for a login, with how loudly it should be said.

    Returns `(metadata, urgency)` where urgency is `notice` or `urgent`. A credential with a
    refresh token is not listed on access-token expiry alone — that is renewed silently and
    reporting it would be crying wolf, which is how a real expiry gets ignored.
    """
    out: list[tuple[TokenMetadata, str]] = []
    for meta in store.list_metadata():
        if meta.has_refresh_token:
            # Renewable. Only the refresh token's own death matters, and providers rarely state
            # its lifetime — so there is nothing to announce until a refresh actually fails.
            continue
        remaining = meta.seconds_remaining
        if remaining is None:
            continue
        if remaining <= URGENT_WINDOW_S:
            out.append((meta, "urgent"))
        elif remaining <= NOTICE_WINDOW_S:
            out.append((meta, "notice"))
    return out
