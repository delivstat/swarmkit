"""Queue observability: Store.queue_stats + GET /queue/stats (queue-observability.md).

The store aggregation runs on SQLite (engine-agnostic — percentiles are computed in Python), so no
Postgres is needed here; the durable-store fallback is what the endpoint reads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert
from swarmkit_runtime.persistence._store import Store, make_engine
from swarmkit_runtime.persistence._tables import jobs


def _store(tmp_path: Path) -> Store:
    return Store(make_engine(f"sqlite:///{tmp_path / 's.sqlite'}"))


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_queue_stats_reports_depth_age_and_latencies(tmp_path: Path) -> None:
    st = _store(tmp_path)
    now = datetime.now(UTC)
    # Uniform keys per executemany batch (SQLAlchemy binds one column set for the list), so the
    # queued/running rows carry explicit None for the lifecycle timestamps and the completed row
    # is inserted on its own.
    with st._engine.begin() as c:
        c.execute(
            insert(jobs),
            [
                {
                    "id": "q1",
                    "topology": "typical",
                    "input": "x",
                    "status": "queued",
                    "created_at": _iso(now - timedelta(seconds=30)),
                    "started_at": None,
                    "completed_at": None,
                },
                {
                    "id": "q2",
                    "topology": "typical",
                    "input": "x",
                    "status": "queued",
                    "created_at": _iso(now - timedelta(seconds=5)),
                    "started_at": None,
                    "completed_at": None,
                },
                {
                    "id": "q3",
                    "topology": "mcp",
                    "input": "x",
                    "status": "queued",
                    "created_at": _iso(now - timedelta(seconds=2)),
                    "started_at": None,
                    "completed_at": None,
                },
                {
                    "id": "r1",
                    "topology": "typical",
                    "input": "x",
                    "status": "running",
                    "created_at": _iso(now),
                    "started_at": None,
                    "completed_at": None,
                },
            ],
        )
        # created -> started is a 2s queue wait; started -> completed is a 1s execution.
        c.execute(
            insert(jobs).values(
                id="c1",
                topology="typical",
                input="x",
                status="completed",
                created_at=_iso(now - timedelta(seconds=10)),
                started_at=_iso(now - timedelta(seconds=8)),
                completed_at=_iso(now - timedelta(seconds=7)),
            )
        )
    s = st.queue_stats()
    assert s["queued"] == 3
    assert s["running"] == 1
    assert s["depth_by_topology"] == {"typical": 2, "mcp": 1}
    assert s["oldest_queued_age_seconds"] >= 29
    assert s["queue_wait_p50_seconds"] == pytest.approx(2.0, abs=0.5)
    assert s["execution_p50_seconds"] == pytest.approx(1.0, abs=0.5)
    assert s["sample_size"] == 1


def test_queue_stats_is_empty_and_safe_on_a_fresh_store(tmp_path: Path) -> None:
    s = _store(tmp_path).queue_stats()
    assert s["queued"] == 0 and s["running"] == 0
    assert s["oldest_queued_age_seconds"] == 0.0
    assert s["queue_wait_p50_seconds"] == 0.0  # empty window, not a crash
    assert s["depth_by_topology"] == {}


@pytest.mark.asyncio
async def test_queue_stats_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: t, name: T}\n"
        "governance: {provider: mock}\n"
    )
    (tmp_path / "topologies" / "hello.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Topology\nmetadata: {name: hello, version: 0.1.0}\n"
        "agents:\n  root: {id: g, role: root, model: {provider: mock, name: m},"
        " prompt: {system: hi}}\n"
    )
    app = create_app(tmp_path, enqueue_only=True)  # enqueue, no worker, so the runs stay queued
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        for _ in range(2):
            await client.post("/run/hello", json={"input": "x"})
        stats: dict[str, Any] = (await client.get("/queue/stats")).json()
    assert stats["queued"] == 2
    assert stats["depth_by_topology"] == {"hello": 2}
