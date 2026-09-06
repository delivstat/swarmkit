"""`GET /events` — the durable pull an application reconciles from.

This is the half that makes a dropped webhook survivable, so the tests are about *resumption*:
that a cursor is stable, that paging from one loses nothing, and that a cursor this API did not
issue is refused rather than silently replaying an application's whole history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from swarmkit_runtime.audit._store import SqlAuditProvider
from swarmkit_runtime.events import CursorError, decode_cursor, encode_cursor
from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.server import create_app


@pytest.fixture
def store(tmp_path: Path) -> SqlAuditProvider:
    return SqlAuditProvider(create_engine(f"sqlite:///{tmp_path / 'audit.db'}"))


async def _record(store: SqlAuditProvider, n: int, *, event_type: str = "run.started") -> None:
    base = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    for i in range(n):
        await store.record(
            AuditEvent(
                event_type=event_type,
                agent_id="root",
                timestamp=base + timedelta(seconds=i),
                run_id=f"run-{i}",
                event_id=uuid.uuid4(),
            )
        )


# ---- the cursor ------------------------------------------------------------------------------


def test_a_cursor_round_trips() -> None:
    c = decode_cursor(encode_cursor("2026-09-06T12:00:00+00:00", "abc-123"))
    assert c.timestamp == "2026-09-06T12:00:00+00:00"
    assert c.event_id == "abc-123"


def test_a_cursor_is_opaque() -> None:
    """A caller that parses it is one that breaks when it becomes a sequence number."""
    encoded = encode_cursor("2026-09-06T12:00:00+00:00", "abc-123")
    assert "2026" not in encoded
    assert "abc-123" not in encoded


def test_a_malformed_cursor_is_refused_not_treated_as_the_beginning() -> None:
    """An application that replayed its whole history over a typo would flood whatever it feeds."""
    with pytest.raises(CursorError, match="not one this API issued"):
        decode_cursor("obviously-not-a-cursor")


# ---- reading in log order --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_come_back_oldest_first(store: SqlAuditProvider) -> None:
    """Ascending, unlike `query`. A consumer replays forward from where it stopped."""
    await _record(store, 5)
    events = await store.since_cursor(limit=10)
    assert [e.run_id for e in events] == ["run-0", "run-1", "run-2", "run-3", "run-4"]


@pytest.mark.asyncio
async def test_paging_from_a_cursor_loses_nothing(store: SqlAuditProvider) -> None:
    await _record(store, 7)
    seen: list[str] = []
    after: tuple[str, str] | None = None
    for _ in range(4):
        page = await store.since_cursor(after=after, limit=3)
        if not page:
            break
        seen.extend(str(e.run_id) for e in page)
        after = (page[-1].timestamp.isoformat(), str(page[-1].event_id))
    assert seen == [f"run-{i}" for i in range(7)]


@pytest.mark.asyncio
async def test_a_cursor_at_the_end_returns_nothing_rather_than_repeating(
    store: SqlAuditProvider,
) -> None:
    await _record(store, 3)
    page = await store.since_cursor(limit=10)
    after = (page[-1].timestamp.isoformat(), str(page[-1].event_id))
    assert await store.since_cursor(after=after, limit=10) == []


@pytest.mark.asyncio
async def test_events_with_identical_timestamps_are_still_totally_ordered(
    store: SqlAuditProvider,
) -> None:
    """The reason the cursor is a pair. Timestamps collide; a cursor that only carried one would
    either skip an event or repeat it forever."""
    same = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
    for i in range(5):
        await store.record(
            AuditEvent(
                event_type="run.started",
                agent_id="root",
                timestamp=same,
                run_id=f"run-{i}",
                event_id=uuid.uuid4(),
            )
        )
    seen: list[str] = []
    after: tuple[str, str] | None = None
    for _ in range(5):
        page = await store.since_cursor(after=after, limit=2)
        if not page:
            break
        seen.extend(str(e.run_id) for e in page)
        after = (page[-1].timestamp.isoformat(), str(page[-1].event_id))
    assert sorted(seen) == [f"run-{i}" for i in range(5)]
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_filtering_by_type_and_run(store: SqlAuditProvider) -> None:
    await _record(store, 3, event_type="run.started")
    await _record(store, 2, event_type="funnel.gate_opened")
    gates = await store.since_cursor(event_types=["funnel.gate_opened"], limit=10)
    assert {e.event_type for e in gates} == {"funnel.gate_opened"}
    one = await store.since_cursor(run_id="run-1", limit=10)
    assert {e.run_id for e in one} == {"run-1"}


# ---- the HTTP surface --------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: t, name: T}\n"
        "governance: {provider: mock, policy_language: yaml}\n"
    )
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_the_route_answers_and_echoes_a_cursor(client: Any) -> None:
    body = client.get("/events?limit=5").json()
    assert "events" in body
    assert "next_cursor" in body
    assert body["has_more"] is False


def test_a_bad_cursor_is_a_400_not_a_replay(client: Any) -> None:
    resp = client.get("/events?after=not-a-cursor")
    assert resp.status_code == 400
    assert "not one this API issued" in resp.text


def test_an_empty_page_echoes_the_cursor_back(client: Any) -> None:
    """So an idle application does not have to remember where it was."""
    cursor = encode_cursor("2099-01-01T00:00:00+00:00", "zzz")
    body = client.get(f"/events?after={cursor}").json()
    assert body["events"] == []
    assert body["next_cursor"] == cursor


def test_limit_is_bounded(client: Any) -> None:
    assert client.get("/events?limit=99999").status_code == 422
