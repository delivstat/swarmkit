"""The durable job queue: atomic claim, lease, heartbeat, reclaim (worker-execution.md).

Postgres-only (SKIP LOCKED). Gated on SWARMKIT_TEST_POSTGRES_URL; each test uses a throwaway schema
so a shared Postgres stays clean.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import event, insert, select, text
from swarmkit_runtime.persistence._store import make_engine
from swarmkit_runtime.persistence._tables import jobs, metadata
from swarmkit_runtime.queue import PostgresJobQueue, QueueUnavailableError


def _pg_url() -> str:
    url = os.environ.get("SWARMKIT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set SWARMKIT_TEST_POSTGRES_URL to run the job-queue tests")
    return url


@pytest.fixture
def engine() -> Any:
    eng = make_engine(_pg_url())
    schema = f"jq_{uuid.uuid4().hex[:12]}"

    # Attach the search_path listener BEFORE any connection is opened, so every connection — the
    # CREATE SCHEMA one included — lands in this test's own schema (the migrate tests' pattern).
    @event.listens_for(eng, "connect")
    def _set_search_path(dbapi_conn: Any, _rec: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute(f"SET search_path TO {schema}")
        cur.close()

    with eng.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()
    metadata.create_all(eng)
    yield eng
    with eng.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    eng.dispose()


def _seed(engine: Any, job_id: str, status: str = "queued", **extra: Any) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(jobs).values(
                id=job_id,
                topology="t",
                input="x",
                status=status,
                created_at=datetime.now(tz=UTC).isoformat(),
                **extra,
            )
        )


def _status(engine: Any, job_id: str) -> tuple[str, str | None, int]:
    with engine.begin() as conn:
        r = conn.execute(
            select(jobs.c.status, jobs.c.worker_id, jobs.c.attempt).where(jobs.c.id == job_id)
        ).first()
    return (str(r[0]), r[1], int(r[2] or 0))


@pytest.mark.asyncio
async def test_claim_takes_the_oldest_queued_and_marks_it_running(engine: Any) -> None:
    _seed(engine, "j1")
    q = PostgresJobQueue(engine, worker_id="w1")
    assert await q.claim(lease_seconds=30) == "j1"
    status, worker, _ = _status(engine, "j1")
    assert status == "running" and worker == "w1"
    assert await q.claim(lease_seconds=30) is None  # nothing left queued


@pytest.mark.asyncio
async def test_two_workers_racing_claim_different_jobs(engine: Any) -> None:
    for i in range(6):
        _seed(engine, f"j{i}")
    a = PostgresJobQueue(engine, worker_id="a")
    b = PostgresJobQueue(engine, worker_id="b")
    claimed = await asyncio.gather(
        *[a.claim(lease_seconds=30) for _ in range(3)],
        *[b.claim(lease_seconds=30) for _ in range(3)],
    )
    got = [c for c in claimed if c]
    assert len(got) == 6 and len(set(got)) == 6, got  # no job claimed twice


@pytest.mark.asyncio
async def test_reclaim_returns_expired_running_and_bumps_attempt(engine: Any) -> None:
    # A running job whose lease is already in the past — an abandoned worker.
    past = "2000-01-01T00:00:00+00:00"
    _seed(engine, "dead", status="running", worker_id="gone", lease_until=past)
    _seed(engine, "alive", status="running", worker_id="w", lease_until="2999-01-01T00:00:00+00:00")
    q = PostgresJobQueue(engine, worker_id="reaper")
    reclaimed = await q.reclaim_expired()
    assert reclaimed == ["dead"]
    status, worker, attempt = _status(engine, "dead")
    assert status == "queued" and worker is None and attempt == 1
    assert _status(engine, "alive")[0] == "running"  # a live lease is untouched


@pytest.mark.asyncio
async def test_complete_drops_the_lease_so_it_is_never_reclaimed(engine: Any) -> None:
    _seed(engine, "j", status="running", worker_id="w", lease_until="2000-01-01T00:00:00+00:00")
    q = PostgresJobQueue(engine, worker_id="w")
    await q.complete("j", status="completed")
    assert _status(engine, "j")[0] == "completed"
    assert await q.reclaim_expired() == []  # terminal, not reclaimed despite the stale lease


@pytest.mark.asyncio
async def test_heartbeat_extends_only_our_own_lease(engine: Any) -> None:
    _seed(engine, "j", status="running", worker_id="w1", lease_until="2000-01-01T00:00:00+00:00")
    await PostgresJobQueue(engine, worker_id="w2").heartbeat("j", lease_seconds=30)
    with engine.begin() as conn:
        lu = conn.execute(select(jobs.c.lease_until).where(jobs.c.id == "j")).scalar()
    assert lu == "2000-01-01T00:00:00+00:00"  # w2 does not hold it, so no extension


@pytest.mark.asyncio
async def test_complete_is_fenced_on_ownership(engine: Any) -> None:
    """A worker whose lease expired and whose run was reclaimed by another cannot complete it — the
    stale completion no-ops (does not clobber the new owner's row or drop its lease). This is what
    stops the reclaim→double-execution cascade a bare ``WHERE id`` update would allow."""
    _seed(engine, "j")
    a = PostgresJobQueue(engine, worker_id="a")
    b = PostgresJobQueue(engine, worker_id="b")

    # A claims with an already-expired lease, then the reaper returns the run to the queue.
    assert await a.claim(lease_seconds=-5) == "j"
    assert await a.reclaim_expired() == ["j"]
    # B claims the reclaimed run and now owns it.
    assert await b.claim(lease_seconds=60) == "j"

    # A (the zombie) tries to finish: fenced out — returns False, changes nothing.
    assert await a.complete("j", status="completed") is False
    status, worker, attempt = _status(engine, "j")
    assert status == "running" and worker == "b" and attempt == 1

    # B, the real owner, completes cleanly.
    assert await b.complete("j", status="completed") is True
    assert _status(engine, "j")[0] == "completed"


@pytest.mark.asyncio
async def test_claim_stamps_claimed_at(engine: Any) -> None:
    """claim records claimed_at so queue-wait (claimed_at - created_at) is recoverable
    (queue-observability.md)."""
    _seed(engine, "j")
    assert await PostgresJobQueue(engine, worker_id="w").claim(lease_seconds=30) == "j"
    with engine.begin() as conn:
        ca = conn.execute(select(jobs.c.claimed_at).where(jobs.c.id == "j")).scalar()
    assert ca is not None


def test_sqlite_refuses_the_queue(tmp_path: Any) -> None:
    eng = make_engine(f"sqlite:///{tmp_path / 'x.sqlite'}")
    with pytest.raises(QueueUnavailableError, match="Postgres"):
        PostgresJobQueue(eng, worker_id="w")
