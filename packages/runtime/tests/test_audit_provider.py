"""Tests for AuditProvider ABC, MockAuditProvider, and SQLiteAuditProvider."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from swarmkit_runtime.audit import (
    MockAuditProvider,
    SQLiteAuditProvider,
    get_registry,
)
from swarmkit_runtime.governance import AuditEvent


def _make_event(
    event_type: str = "test.event",
    agent_id: str = "agent-1",
    run_id: str = "run-001",
    **kwargs: object,
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        agent_id=agent_id,
        timestamp=datetime.now(tz=UTC),
        run_id=run_id,
        **kwargs,  # type: ignore[arg-type]
    )


class TestMockAuditProvider:
    @pytest.mark.asyncio
    async def test_record_and_query(self) -> None:
        provider = MockAuditProvider()
        event = _make_event()
        await provider.record(event)

        results = [e async for e in provider.query(run_id="run-001")]
        assert len(results) == 1
        assert results[0].event_type == "test.event"

    @pytest.mark.asyncio
    async def test_query_filters(self) -> None:
        provider = MockAuditProvider()
        await provider.record(_make_event(agent_id="a"))
        await provider.record(_make_event(agent_id="b"))
        await provider.record(_make_event(agent_id="a", event_type="other"))

        results = [e async for e in provider.query(agent_id="a")]
        assert len(results) == 2

        results = [e async for e in provider.query(event_type="other")]
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_count(self) -> None:
        provider = MockAuditProvider()
        await provider.record(_make_event(run_id="r1"))
        await provider.record(_make_event(run_id="r1"))
        await provider.record(_make_event(run_id="r2"))

        assert await provider.count(run_id="r1") == 2
        assert await provider.count(run_id="r2") == 1
        assert await provider.count() == 3

    @pytest.mark.asyncio
    async def test_query_limit(self) -> None:
        provider = MockAuditProvider()
        for _ in range(10):
            await provider.record(_make_event())

        results = [e async for e in provider.query(limit=3)]
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_events_property(self) -> None:
        provider = MockAuditProvider()
        await provider.record(_make_event())
        assert len(provider.events) == 1


class TestSQLiteAuditProvider:
    @pytest.mark.asyncio
    async def test_record_and_query(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.sqlite"
        provider = SQLiteAuditProvider(db_path=db)

        event = _make_event(
            model_provider="anthropic",
            model_name="claude-sonnet-4-6",
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.002,
            duration_ms=1500,
        )
        await provider.record(event)

        results = [e async for e in provider.query(run_id="run-001")]
        assert len(results) == 1
        assert results[0].event_type == "test.event"
        assert results[0].model_provider == "anthropic"
        assert results[0].tokens_in == 100
        assert results[0].cost_usd == 0.002
        assert results[0].duration_ms == 1500
        await provider.close()

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.sqlite"

        provider1 = SQLiteAuditProvider(db_path=db)
        await provider1.record(_make_event())
        await provider1.close()

        provider2 = SQLiteAuditProvider(db_path=db)
        assert await provider2.count() == 1
        await provider2.close()

    @pytest.mark.asyncio
    async def test_query_filters(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.sqlite"
        provider = SQLiteAuditProvider(db_path=db)

        await provider.record(_make_event(agent_id="a", run_id="r1"))
        await provider.record(_make_event(agent_id="b", run_id="r1"))
        await provider.record(_make_event(agent_id="a", run_id="r2"))

        results = [e async for e in provider.query(agent_id="a")]
        assert len(results) == 2

        results = [e async for e in provider.query(run_id="r1")]
        assert len(results) == 2

        results = [e async for e in provider.query(agent_id="b", run_id="r1")]
        assert len(results) == 1
        await provider.close()

    @pytest.mark.asyncio
    async def test_count(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.sqlite"
        provider = SQLiteAuditProvider(db_path=db)

        await provider.record(_make_event(run_id="r1"))
        await provider.record(_make_event(run_id="r1"))
        await provider.record(_make_event(run_id="r2"))

        assert await provider.count(run_id="r1") == 2
        assert await provider.count() == 3
        await provider.close()

    @pytest.mark.asyncio
    async def test_duplicate_event_id_ignored(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.sqlite"
        provider = SQLiteAuditProvider(db_path=db)

        event = _make_event()
        await provider.record(event)
        await provider.record(event)  # same event_id

        assert await provider.count() == 1
        await provider.close()

    @pytest.mark.asyncio
    async def test_json_fields_roundtrip(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.sqlite"
        provider = SQLiteAuditProvider(db_path=db)

        event = _make_event(
            inputs={"query": "hello", "context": [1, 2, 3]},
            outputs={"answer": "world"},
            error={"type": "ValueError", "message": "bad input"},
            payload={"extra": "data"},
        )
        await provider.record(event)

        results = [e async for e in provider.query()]
        assert results[0].inputs == {"query": "hello", "context": [1, 2, 3]}
        assert results[0].outputs == {"answer": "world"}
        assert results[0].error == {"type": "ValueError", "message": "bad input"}
        assert results[0].payload == {"extra": "data"}
        await provider.close()

    @pytest.mark.asyncio
    async def test_prune_expired(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.sqlite"
        provider = SQLiteAuditProvider(db_path=db, retention_days=7)

        old_event = AuditEvent(
            event_type="old",
            agent_id="a",
            timestamp=datetime.now(tz=UTC) - timedelta(days=10),
            run_id="old-run",
        )
        new_event = _make_event(run_id="new-run")

        await provider.record(old_event)
        await provider.record(new_event)

        pruned = await provider.prune_expired()
        assert pruned == 1
        assert await provider.count() == 1

        results = [e async for e in provider.query()]
        assert results[0].run_id == "new-run"
        await provider.close()

    @pytest.mark.asyncio
    async def test_query_since_until(self, tmp_path: Path) -> None:
        db = tmp_path / "audit.sqlite"
        provider = SQLiteAuditProvider(db_path=db)

        t1 = datetime(2026, 5, 1, tzinfo=UTC)
        t2 = datetime(2026, 5, 5, tzinfo=UTC)
        t3 = datetime(2026, 5, 10, tzinfo=UTC)

        await provider.record(AuditEvent(event_type="e1", agent_id="a", timestamp=t1, run_id="r"))
        await provider.record(AuditEvent(event_type="e2", agent_id="a", timestamp=t2, run_id="r"))
        await provider.record(AuditEvent(event_type="e3", agent_id="a", timestamp=t3, run_id="r"))

        results = [e async for e in provider.query(since=datetime(2026, 5, 3, tzinfo=UTC))]
        assert len(results) == 2

        results = [e async for e in provider.query(until=datetime(2026, 5, 6, tzinfo=UTC))]
        assert len(results) == 2
        await provider.close()


class TestRegistry:
    def test_built_in_providers_registered(self) -> None:
        reg = get_registry()
        assert "mock" in reg.available()
        assert "sqlite" in reg.available()

    def test_get_returns_class(self) -> None:
        reg = get_registry()
        assert reg.get("mock") is MockAuditProvider
        assert reg.get("sqlite") is SQLiteAuditProvider
        assert reg.get("nonexistent") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_postgres_audit_roundtrip() -> None:
    """The same audit provider on a real Postgres — runs only when SWARMKIT_TEST_POSTGRES_URL is
    set (deselected by default; guards the dialect end-to-end)."""
    import os  # noqa: PLC0415

    from swarmkit_runtime.audit import PostgresAuditProvider  # noqa: PLC0415

    url = os.environ.get("SWARMKIT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set SWARMKIT_TEST_POSTGRES_URL to run the Postgres audit test")
    provider = PostgresAuditProvider(url)
    try:
        ev = AuditEvent(
            event_type="agent.completed",
            agent_id="root",
            timestamp=datetime.now(UTC),
            run_id="pg-run-1",
            duration_ms=7,
        )
        await provider.record(ev)
        await provider.record(ev)  # duplicate PK → deduped, no raise
        assert await provider.count(run_id="pg-run-1") == 1
        events = [e async for e in provider.query(run_id="pg-run-1")]
        assert events[0].agent_id == "root" and events[0].duration_ms == 7
    finally:
        await provider.close()
