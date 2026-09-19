"""A durable job queue so worker processes claim and execute runs (worker-execution.md).

The default is Postgres: the ``jobs`` row *is* the queue entry, so there is one durable source of
truth — the run, its audit and its gates already live there. The claim is the proven
``SELECT … FOR UPDATE SKIP LOCKED`` pattern; a lease + heartbeat lets a crashed worker's run be
reclaimed by another. SQLite cannot back this (a single file writer serializes across processes), so
it refuses with a clear message rather than corrupting under contention.

This is the queue mechanism only. Wiring serve into an API tier and a `swarmkit worker` command is a
separate slice; a single `swarmkit serve` keeps executing in-process and does not touch this.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine, select, update

from swarmkit_runtime.persistence._tables import jobs

#: Statuses the queue owns. Terminal ones (completed/failed/stopped) and `deferred` (parked on a
#: human gate, re-enqueued on resume) are never reclaimed — only a `running` lease can expire.
QUEUED = "queued"
RUNNING = "running"


class QueueUnavailableError(RuntimeError):
    """The store cannot back a durable queue (SQLite is single-writer; workers need Postgres)."""


class JobQueue(Protocol):
    async def enqueue(self, job_id: str) -> None: ...
    async def claim(self, *, lease_seconds: int) -> str | None: ...
    async def heartbeat(self, job_id: str, *, lease_seconds: int) -> None: ...
    async def complete(self, job_id: str, *, status: str) -> bool: ...
    async def reclaim_expired(self) -> list[str]: ...


def _now() -> datetime:
    return datetime.now(tz=UTC)


class PostgresJobQueue:
    """A ``JobQueue`` over the Postgres ``jobs`` table (worker-execution.md).

    ``worker_id`` identifies this worker in claims and heartbeats. Every DB call runs off the event
    loop (``asyncio.to_thread``) so a worker's loop stays responsive while it polls/claims.
    """

    def __init__(self, engine: Engine, *, worker_id: str) -> None:
        if engine.dialect.name != "postgresql":
            raise QueueUnavailableError(
                "the durable job queue requires Postgres — SQLite is a single writer and cannot be "
                "claimed across worker processes. Point storage at Postgres to run workers."
            )
        self._engine = engine
        self._worker_id = worker_id

    async def enqueue(self, job_id: str) -> None:
        def _run() -> None:
            with self._engine.begin() as conn:
                conn.execute(
                    update(jobs).where(jobs.c.id == job_id).values(status=QUEUED, lease_until=None)
                )

        await asyncio.to_thread(_run)

    async def claim(self, *, lease_seconds: int) -> str | None:
        """Atomically claim the oldest queued job and mark it running with a lease. None if empty.

        The ``SELECT … FOR UPDATE SKIP LOCKED`` + ``UPDATE`` in one transaction is the claim: two
        workers racing take two different rows (or one takes it and the other sees none).
        """

        def _run() -> str | None:
            until = (_now() + timedelta(seconds=lease_seconds)).isoformat()
            with self._engine.begin() as conn:
                row = conn.execute(
                    select(jobs.c.id)
                    .where(jobs.c.status == QUEUED)
                    .order_by(jobs.c.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                ).first()
                if row is None:
                    return None
                job_id = str(row[0])
                conn.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id)
                    .values(status=RUNNING, worker_id=self._worker_id, lease_until=until)
                )
                return job_id

        return await asyncio.to_thread(_run)

    async def heartbeat(self, job_id: str, *, lease_seconds: int) -> None:
        """Extend the lease while the run is still executing. No-op if we no longer hold it."""

        def _run() -> None:
            until = (_now() + timedelta(seconds=lease_seconds)).isoformat()
            with self._engine.begin() as conn:
                conn.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id, jobs.c.worker_id == self._worker_id)
                    .values(lease_until=until)
                )

        await asyncio.to_thread(_run)

    async def complete(self, job_id: str, *, status: str) -> bool:
        """Mark a run terminal (completed/failed/stopped/deferred) and drop its lease so the reaper
        never reclaims it. Returns whether this worker still owned the job.

        Fenced on ``worker_id``: if our lease had already expired and the reaper handed the run to
        another worker (which set its own ``worker_id``), our completion matches no row and is
        discarded — the winner's in-flight run is not clobbered by our stale result. Without this
        guard a ``WHERE id = :id`` update would let a lease-expired worker overwrite the row the
        current owner is still writing to (the double-execution race worker-execution.md names)."""

        def _run() -> bool:
            with self._engine.begin() as conn:
                result = conn.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id, jobs.c.worker_id == self._worker_id)
                    .values(status=status, worker_id=None, lease_until=None)
                )
                # psycopg reports -1 for "unknown" on some paths; treat only a definite 0 as "lost".
                return result.rowcount != 0

        return await asyncio.to_thread(_run)

    async def reclaim_expired(self) -> list[str]:
        """Return running jobs whose lease has passed to the queue, bumping ``attempt``.

        A crashed worker leaves its run ``running`` with a stale lease; another worker (or serve)
        calls this, and the run is re-claimed and resumed from its checkpoint — not restarted, and
        not double-executed, because a resume continues from durable state and the attempt bump
        distinguishes it from the abandoned try.
        """

        def _run() -> list[str]:
            now = _now().isoformat()
            with self._engine.begin() as conn:
                rows = conn.execute(
                    select(jobs.c.id)
                    .where(jobs.c.status == RUNNING, jobs.c.lease_until < now)
                    .with_for_update(skip_locked=True)
                ).fetchall()
                ids = [str(r[0]) for r in rows]
                for job_id in ids:
                    conn.execute(
                        update(jobs)
                        .where(jobs.c.id == job_id)
                        .values(
                            status=QUEUED,
                            worker_id=None,
                            lease_until=None,
                            attempt=(jobs.c.attempt + 1),
                        )
                    )
                return ids

        return await asyncio.to_thread(_run)
