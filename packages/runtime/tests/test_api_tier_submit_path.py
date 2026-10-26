"""The API tier's per-submit work (issue 1084, design/details/api-tier-submit-path.md).

`POST /run` on `serve --role api` used to make ~23 synchronous database round trips on the event
loop — a ``COUNT(*)`` for the queue bound, an insert followed by up to two updates of the same row,
an access-audit insert — and render its log line through rich. These pin each piece of the fix:

- the job row is written by **one** insert that already carries ``queued``, class and priority;
- the queue bound reads the backlog at most once per window, and concurrent submits still see each
  other's places;
- access-audit rows are buffered, stamped at record time, and written in batches — never dropped on
  a clean shutdown, kept for retry when the store fails;
- serve's root logger is not a ``RichHandler``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from swarmkit_runtime.persistence import SqliteStore
from swarmkit_runtime.server import _services
from swarmkit_runtime.server._access_audit import AccessAuditWriter
from swarmkit_runtime.server._services import _QueueDepthGauge

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


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "hello.yaml").write_text(_TOPO)
    return tmp_path


class _Spy:
    """Wraps a store and counts the calls the submit path makes."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapped(*a: Any, **k: Any) -> Any:
            self.calls.append(name)
            return attr(*a, **k)

        return wrapped


# ---- one insert per submit ---------------------------------------------------------------------


def test_create_job_writes_the_enqueue_fields_in_the_insert(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    store.create_job(
        "j1", "hello", "hi", status="queued", version="v2", job_class="harness", priority=5
    )
    row = store.get_job("j1")
    assert row is not None
    assert (row.status, row.version, row.job_class, row.priority) == ("queued", "v2", "harness", 5)


def test_create_job_defaults_are_unchanged(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    store.create_job("j1", "hello", "hi")
    row = store.get_job("j1")
    assert row is not None
    assert (row.status, row.version, row.job_class, row.priority) == ("pending", None, None, 0)


@pytest.mark.asyncio
async def test_a_queue_mode_submit_is_one_insert_and_no_update(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws, enqueue_only=True, queue_max_depth=100)
    async with app.router.lifespan_context(app):
        spy = _Spy(app.state.store)
        app.state.store = spy
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            r = await client.post("/run/hello", json={"input": "hi", "labels": {"priority": "7"}})
        assert r.status_code == 200, r.text
        assert spy.calls.count("create_job") == 1
        assert "update_job" not in spy.calls
        row = spy.get_job(r.json()["job_id"])
        assert (row.status, row.job_class, row.priority) == ("queued", "model", 7)


# ---- the queue bound ---------------------------------------------------------------------------


class _CountingStore:
    def __init__(self, depth: int) -> None:
        self.depth = depth
        self.reads = 0

    def count_jobs(self, status: str) -> int:
        assert status == "queued"
        self.reads += 1
        return self.depth


def test_the_backlog_is_read_at_most_once_per_window(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [100.0]
    store = _CountingStore(depth=3)
    gauge = _QueueDepthGauge(clock=lambda: now[0])
    for _ in range(50):
        assert gauge.depth(store) == 3  # type: ignore[arg-type]
    assert store.reads == 1
    now[0] += _services.QUEUE_DEPTH_MAX_AGE_S
    store.depth = 9
    assert gauge.depth(store) == 9  # type: ignore[arg-type]
    assert store.reads == 2


def test_a_reserved_place_is_seen_before_the_next_read(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _CountingStore(depth=1)
    gauge = _QueueDepthGauge(clock=lambda: 0.0)
    assert gauge.depth(store) == 1  # type: ignore[arg-type]
    gauge.reserve()
    gauge.reserve()
    assert gauge.depth(store) == 3  # type: ignore[arg-type]
    gauge.release()
    gauge.landed()
    assert gauge.depth(store) == 2  # type: ignore[arg-type]
    assert store.reads == 1


def test_a_re_read_still_counts_inserts_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overshoot this guards: a re-read sees only committed rows. Had in-flight places been
    folded into the reading, the re-read would forget them and admit past the bound."""
    now = [0.0]
    store = _CountingStore(depth=0)
    gauge = _QueueDepthGauge(clock=lambda: now[0])
    gauge.depth(store)  # type: ignore[arg-type]
    gauge.reserve()
    gauge.reserve()  # two inserts in flight, neither committed
    now[0] += _services.QUEUE_DEPTH_MAX_AGE_S
    assert gauge.depth(store) == 2  # type: ignore[arg-type]
    assert store.reads == 2
    # They land; the next read sees them committed, and they are not counted twice.
    gauge.landed()
    gauge.landed()
    now[0] += _services.QUEUE_DEPTH_MAX_AGE_S
    store.depth = 2
    assert gauge.depth(store) == 2  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_concurrent_submits_do_not_overshoot_the_bound(ws: Path) -> None:
    """Twenty submits in flight at once against a bound of 5: the inserts run off the loop, so all
    twenty pass the check before any insert lands — reservations are what keep it at five."""
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws, enqueue_only=True, queue_max_depth=5)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            rs = await asyncio.gather(
                *(client.post("/run/hello", json={"input": "x"}) for _ in range(20))
            )
        codes = sorted(r.status_code for r in rs)
        assert codes == [200] * 5 + [429] * 15, codes
        assert app.state.store.count_jobs("queued") == 5


@pytest.mark.asyncio
async def test_a_failed_insert_gives_its_place_back(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws, enqueue_only=True, queue_max_depth=1)
    async with app.router.lifespan_context(app):
        real = app.state.store
        failing = _Spy(real)

        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("db down")

        setattr(failing, "create_job", boom)  # noqa: B010 — shadows the spy's __getattr__
        app.state.store = failing
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://127.0.0.1",
        ) as client:
            assert (await client.post("/run/hello", json={"input": "x"})).status_code == 500
            delattr(failing, "create_job")
            # Same store object, so the same gauge: had the place not been given back, a bound of
            # one would refuse this.
            assert (await client.post("/run/hello", json={"input": "x"})).status_code == 200


# ---- access audit ------------------------------------------------------------------------------


def _row(path: str = "/run/hello", status: int = 200) -> dict[str, Any]:
    return {
        "client_id": "c",
        "provider": "api_key",
        "method": "POST",
        "path": path,
        "action": "run",
        "status": status,
    }


@pytest.mark.asyncio
async def test_recording_does_no_io_and_flush_writes_one_batch(tmp_path: Path) -> None:
    spy = _Spy(SqliteStore(tmp_path))
    writer = AccessAuditWriter(spy)
    for i in range(30):
        writer.record(**_row(path=f"/run/t{i}"))
    assert spy.calls == []
    await writer.flush()
    assert spy.calls == ["record_access_many"]
    rows = spy.list_access(limit=100)
    assert len(rows) == 30
    assert {r["path"] for r in rows} == {f"/run/t{i}" for i in range(30)}


@pytest.mark.asyncio
async def test_a_row_is_stamped_when_recorded_not_when_written(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    writer = AccessAuditWriter(store)
    writer.record(**_row())
    stamped = writer._buffer[0]["created_at"]
    await asyncio.sleep(0.02)
    await writer.flush()
    assert store.list_access(limit=1)[0]["created_at"] == stamped


@pytest.mark.asyncio
async def test_a_failed_flush_keeps_the_rows_for_the_next_one(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    attempts = [0]
    real = store.record_access_many

    def flaky(records: list[dict[str, Any]]) -> None:
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("db down")
        real(records)

    store.record_access_many = flaky  # type: ignore[method-assign]
    writer = AccessAuditWriter(store)
    writer.record(**_row(path="/a"))
    await writer.flush()
    assert store.list_access() == []
    writer.record(**_row(path="/b"))
    await writer.flush()
    assert [r["path"] for r in store.list_access()] == ["/b", "/a"]


@pytest.mark.asyncio
async def test_the_periodic_flush_writes_and_stop_writes_the_rest(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path)
    writer = AccessAuditWriter(store, flush_interval_s=0.01)
    writer.start()
    writer.record(**_row(path="/early"))
    for _ in range(100):
        if store.list_access():
            break
        await asyncio.sleep(0.01)
    assert [r["path"] for r in store.list_access()] == ["/early"]
    writer.record(**_row(path="/late"))
    await writer.stop()
    assert {r["path"] for r in store.list_access()} == {"/early", "/late"}


# ---- logging -----------------------------------------------------------------------------------


def test_serve_does_not_log_through_rich(ws: Path) -> None:
    pytest.importorskip("mcp")
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    try:
        create_app(ws)
        names = [type(h).__name__ for h in root.handlers]
        assert names, "serve should leave the root logger a handler"
        assert "RichHandler" not in names, names
    finally:
        root.handlers = saved


def test_an_embedder_keeps_its_own_logging(ws: Path) -> None:
    pytest.importorskip("mcp")
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    root = logging.getLogger()
    saved = root.handlers[:]
    mine = logging.NullHandler()
    root.handlers = [mine]
    try:
        create_app(ws)
        assert root.handlers == [mine]
    finally:
        root.handlers = saved


# ---- cancellation mid-insert -------------------------------------------------------------------
#
# The insert runs in a worker thread, and a thread cannot be cancelled: if the request is cancelled
# while it runs (a graceful-shutdown timeout, a cancelling middleware), the row lands anyway. The
# submit therefore finishes what it started, as the old on-loop insert did by having no
# cancellation point at all — otherwise an all-in-one run is left `pending` with nothing to run it.


class _SlowStore:
    """``create_job`` signals when it is entered and when the real insert has returned, so the
    test cancels inside the insert and checks after it — never by guessing how long either takes
    on the machine it runs on (#1102)."""

    def __init__(self, inner: Any, delay: float) -> None:
        self._inner = inner
        self._delay = delay
        self.entered = threading.Event()
        self.inserted = threading.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def create_job(self, *a: Any, **k: Any) -> Any:
        import time  # noqa: PLC0415

        self.entered.set()
        time.sleep(self._delay)
        try:
            return self._inner.create_job(*a, **k)
        finally:
            self.inserted.set()


async def _settled(timeout: float = 10.0) -> None:
    """Wait for the shielded rest of the submit — it runs on the loop as its own task, after the
    request that started it was cancelled."""
    me = asyncio.current_task()
    deadline = asyncio.get_running_loop().time() + timeout
    while any(t is not me and not t.done() for t in asyncio.all_tasks()):
        assert asyncio.get_running_loop().time() < deadline, "the submit never settled"
        await asyncio.sleep(0.01)


async def _start_and_cancel(
    store: _SlowStore,
    *,
    enqueue_only: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Any], Any]:
    from swarmkit_runtime.server._config import ServerCfg  # noqa: PLC0415
    from swarmkit_runtime.server._jobs import JobStore  # noqa: PLC0415
    from swarmkit_runtime.server._services import JobService  # noqa: PLC0415

    launched: list[Any] = []
    monkeypatch.setattr(_services, "_start_job", lambda *a, **k: launched.append(a[1].id))

    class _RT:
        workspace = type("W", (), {"topologies": {"hello": object()}})()
        workspace_root = Path(".")

    jobs = JobStore()
    task = asyncio.create_task(
        JobService(jobs).start(
            rt=_RT(),  # type: ignore[arg-type]
            canary=None,
            store=store,  # type: ignore[arg-type]
            cfg=ServerCfg(),
            semaphore=None,
            topology_name="hello",
            user_input="hi",
            max_steps=5,
            enqueue_only=enqueue_only,
            queue_max_depth=10 if enqueue_only else 0,
            job_id="cancelled-1",
        )
    )
    assert await asyncio.to_thread(store.entered.wait, 10), "the submit never reached the insert"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(store.inserted.wait, 10), "the thread's insert never finished"
    await _settled()  # and whatever follows it, on the loop
    return launched, jobs


@pytest.mark.asyncio
async def test_a_cancelled_all_in_one_submit_still_starts_the_row_it_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _SlowStore(SqliteStore(tmp_path), delay=0.2)
    launched, _ = await _start_and_cancel(store, enqueue_only=False, monkeypatch=monkeypatch)
    row = store.get_job("cancelled-1")
    assert row is not None and row.status == "pending"
    assert launched == ["cancelled-1"], "a written row nobody starts is stuck `pending`"


@pytest.mark.asyncio
async def test_a_cancelled_queue_submit_leaves_a_claimable_row_and_counts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inner = SqliteStore(tmp_path)
    store = _SlowStore(inner, delay=0.2)
    # Never re-read during the test: the depth seen is the gauge's own accounting.
    monkeypatch.setattr(_services, "QUEUE_DEPTH_MAX_AGE_S", 3600.0)
    _, jobs = await _start_and_cancel(store, enqueue_only=True, monkeypatch=monkeypatch)
    row = inner.get_job("cancelled-1")
    assert row is not None and row.status == "queued"
    # The row is in the queue, so the bound must count it — not give its place back.
    assert _services._queue_gauge(store).depth(store) == 1  # type: ignore[arg-type]
    assert await jobs.get("cancelled-1") is None  # no in-memory stub left behind


@pytest.mark.asyncio
async def test_stop_during_a_slow_flush_loses_and_duplicates_nothing(tmp_path: Path) -> None:
    import time  # noqa: PLC0415

    store = SqliteStore(tmp_path)
    real = store.record_access_many

    def slow(records: list[dict[str, Any]]) -> None:
        time.sleep(0.2)
        real(records)

    store.record_access_many = slow  # type: ignore[method-assign]
    writer = AccessAuditWriter(store, flush_interval_s=0.01)
    writer.start()
    writer.record(**_row(path="/first"))
    await asyncio.sleep(0.05)  # the periodic flush is now inside the slow write
    writer.record(**_row(path="/second"))
    await writer.stop()
    paths = [r["path"] for r in store.list_access()]
    assert sorted(paths) == ["/first", "/second"], paths
    assert paths == ["/second", "/first"], "newest first, in the order they were recorded"


# ---- the opt-out: --access-audit sync ----------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_access_audit_writes_the_row_before_the_response(ws: Path) -> None:
    """`sync` is the pre-1.263.3 behaviour: no writer, and the row is committed by the time the
    caller has its response — so a kill -9 after the response cannot lose it."""
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws, enqueue_only=True, access_audit="sync")
    async with app.router.lifespan_context(app):
        assert app.state.access_audit is None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            r = await client.post("/run/hello", json={"input": "x"})
        assert r.status_code == 200
        # Read inside the lifespan, before any shutdown flush could have written it.
        assert any(
            row["path"] == "/run/hello" and row["status"] == 200
            for row in app.state.store.list_access(limit=10)
        )


@pytest.mark.asyncio
async def test_batched_is_the_default(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(ws, enqueue_only=True)
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.access_audit, AccessAuditWriter)


def test_an_unknown_access_audit_mode_is_refused(ws: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with pytest.raises(ValueError, match="access_audit"):
        create_app(ws, access_audit="sometimes")


@pytest.mark.parametrize("how", ["flag", "env"])
def test_serve_refuses_an_unknown_mode_from_flag_or_env(
    ws: Path, how: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swarmkit_runtime.cli import app as cli  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    args = ["serve", str(ws)]
    if how == "flag":
        args += ["--access-audit", "sometimes"]
    else:
        monkeypatch.setenv("SWARMKIT_ACCESS_AUDIT", "sometimes")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2, result.output
    assert "invalid access audit mode 'sometimes'" in result.output


def test_serve_passes_the_mode_through(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The env var reaches create_app, so a container sets it without changing its command."""
    import uvicorn  # noqa: PLC0415
    from swarmkit_runtime import server  # noqa: PLC0415
    from swarmkit_runtime.cli import app as cli  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    seen: dict[str, Any] = {}
    real = server.create_app

    def spy(*a: Any, **k: Any) -> Any:
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(server, "create_app", spy)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setenv("SWARMKIT_ACCESS_AUDIT", "sync")
    result = CliRunner().invoke(cli, ["serve", str(ws), "--host", "127.0.0.1"])
    assert result.exit_code == 0, result.output
    assert seen["access_audit"] == "sync"
    assert "access audit: sync" in result.output
