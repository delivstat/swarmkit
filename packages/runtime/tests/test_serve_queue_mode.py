"""The API/worker split: `serve --role api` enqueues, `swarmkit worker` runs (worker-execution.md).

Two levels:

- **Enqueue only** (SQLite, always runs): an app built with ``enqueue_only=True`` persists a run as
  ``queued`` and returns without executing it — the job stays ``queued`` because nothing drains it.
- **Worker end-to-end** (Postgres, gated on ``SWARMKIT_TEST_POSTGRES_URL``): an api-tier app
  enqueues a run, a worker claims and runs it, and the api tier reports ``completed``. The two
  tiers share one Postgres and never execute in the same process.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from swarmkit_runtime.persistence._store import make_engine

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: t, name: T}
governance: {provider: mock}
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: hello, version: 0.1.0}
agents:
  root:
    id: greeter
    role: root
    model: {provider: mock, name: m}
    prompt: {system: greet}
"""


def _write_workspace(root: Path) -> Path:
    (root / "topologies").mkdir()
    (root / "workspace.yaml").write_text(_WS)
    (root / "topologies" / "hello.yaml").write_text(_TOPO)
    return root


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    return _write_workspace(tmp_path)


async def _submit(app: Any, topology: str = "hello") -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        r = await client.post(f"/run/{topology}", json={"input": "hi"})
        assert r.status_code == 200, r.text
        return dict(r.json())


async def _get(app: Any, job_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return dict((await client.get(f"/jobs/{job_id}")).json())


@pytest.mark.asyncio
async def test_enqueue_only_persists_queued_and_does_not_execute(ws: Path) -> None:
    """`serve --role api` writes the job down as queued and returns; nothing runs it."""
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws, enqueue_only=True)
    async with app.router.lifespan_context(app):
        submitted = await _submit(app)
        assert submitted["status"] == "queued"
        job_id = submitted["job_id"]

        # The durable row is the queue entry a worker will claim.
        durable = app.state.store.get_job(job_id)
        assert durable is not None and durable.status == "queued"

        # No worker is draining, so it stays queued — the api tier never executes.
        await asyncio.sleep(0.3)
        assert (await _get(app, job_id))["status"] == "queued"
        assert app.state.store.get_job(job_id).status == "queued"


@pytest.mark.asyncio
async def test_all_in_one_still_executes(ws: Path) -> None:
    """The default (no role) is unchanged: submit runs to completion in-process."""
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws)  # enqueue_only defaults False
    async with app.router.lifespan_context(app):
        job_id = (await _submit(app))["job_id"]
        for _ in range(400):
            if (await _get(app, job_id))["status"] == "completed":
                break
            await asyncio.sleep(0.05)
        assert (await _get(app, job_id))["status"] == "completed"


@pytest.mark.asyncio
async def test_worker_refuses_sqlite(ws: Path) -> None:
    """The worker model is Postgres-only; a SQLite store is refused, not corrupted by contention."""
    from swarmkit_runtime.queue import QueueUnavailableError, run_worker  # noqa: PLC0415

    with pytest.raises(QueueUnavailableError):
        await run_worker(ws, once=True)


# ---- worker end-to-end (Postgres) ----------------------------------------


def _pg_base_url() -> str:
    url = os.environ.get("SWARMKIT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set SWARMKIT_TEST_POSTGRES_URL to run the worker end-to-end test")
    return url


@pytest.fixture
def pg_ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A workspace whose store is a throwaway Postgres schema, set via SWARMKIT_STORE_URL.

    The schema is baked into the URL (``options=-csearch_path=<schema>``) so *every* engine built
    from it — the api tier's, the worker's, the queue's — lands in this test's own schema, without
    needing a per-engine connect listener the internal engines would not carry.
    """
    base = _pg_base_url()
    schema = f"qm_{uuid.uuid4().hex[:12]}"
    sep = "&" if "?" in base else "?"
    scoped = f"{base}{sep}options=-csearch_path%3D{schema}"

    admin = make_engine(base)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    monkeypatch.setenv("SWARMKIT_STORE_URL", scoped)
    _write_workspace(tmp_path)
    yield tmp_path
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    admin.dispose()


@pytest.mark.asyncio
async def test_api_stream_follows_worker_job_to_completion(pg_ws: Path) -> None:
    """GET /jobs/{id}/stream on the API tier follows a worker's job — it does not replay a
    `queued` snapshot and close. The stream is opened while the job is still queued; a worker runs
    it concurrently; the stream must end with `[done] status=completed`, proving it followed the
    durable row rather than closing on the enqueue-time state."""
    from swarmkit_runtime.queue import run_worker  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(pg_ws, enqueue_only=True)
    async with app.router.lifespan_context(app):
        job_id = (await _submit(app))["job_id"]
        assert app.state.store.get_job(job_id).status == "queued"

        async def read_stream() -> str:
            last_done = ""
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", timeout=30
                ) as client,
                client.stream("GET", f"/jobs/{job_id}/stream") as r,
            ):
                async for line in r.aiter_lines():
                    if line.startswith("data: [done] status="):
                        last_done = line.split("status=", 1)[1].strip()
            return last_done

        async def run_worker_soon() -> None:
            await asyncio.sleep(0.2)  # open the stream on a still-queued job first
            await run_worker(pg_ws, once=True, poll_seconds=0.05)

        done_status, _ = await asyncio.gather(read_stream(), run_worker_soon())
        assert done_status == "completed", done_status


@pytest.mark.asyncio
async def test_api_enqueues_and_worker_drains(pg_ws: Path) -> None:
    """POST /run enqueues; a worker claims + runs it; the api tier then sees completed."""
    from swarmkit_runtime.queue import run_worker  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(pg_ws, enqueue_only=True)
    async with app.router.lifespan_context(app):
        job_id = (await _submit(app))["job_id"]
        assert app.state.store.get_job(job_id).status == "queued"

        # A worker in this process, draining a single job — the same run path serve would use.
        executed = await run_worker(pg_ws, once=True, poll_seconds=0.05)
        assert executed == 1

        # The api tier, reading the shared Postgres, now reports the worker's result.
        final = await _get(app, job_id)
        assert final["status"] == "completed", final
        assert final["output"]
