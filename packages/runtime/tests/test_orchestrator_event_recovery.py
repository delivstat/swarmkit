"""One transient error must not halt the pipeline permanently.

Reported against 1.137.0. `run_drive_loop()` had no error handling around `handle_event`, so any
exception escaped the loop, escaped `asyncio.run()`, and the process exited — after the event was
claimed and before it was acked. Since `claim()` only ever selected `queued` rows, and there was no
`claimed_at`, no visibility timeout and no reclaim path, that event was then unreachable by any
restart. The orchestrator polled past it forever while the saga sat `active` with `updated_at`
frozen at the crash, and `pipeline status` showed a normal in-progress run.

Hit by WSL's `autoProxy` pointing `HTTP_PROXY` at a dead port, so the orchestrator's **loopback**
call to serve raised `ConnectError`. It read as a slow stage for over an hour; recovery took direct
SQL, because re-emitting is refused for an active saga and there was no gate to clear.

The docstring claimed "a crash re-drives from the store" — the one case that could not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from swarmkit_runtime.cli._cmd_orchestrator import (
    _DEFAULT_MAX_ATTEMPTS,
    default_worker_name,
    run_drive_loop,
)
from swarmkit_runtime.orchestration._saga_store import SqlSagaStore
from swarmkit_runtime.persistence._store import make_engine


def _store(tmp_path: Path) -> SqlSagaStore:
    return SqlSagaStore(make_engine(f"sqlite:///{tmp_path / 'saga.sqlite'}"))


class _Controller:
    """A controller whose handler fails a given number of times, then succeeds."""

    def __init__(self, failures: int = 0, exc: Exception | None = None) -> None:
        self.failures = failures
        self.exc = exc or RuntimeError("boom")
        self.calls = 0

    async def handle_event(self, _correlation_id: str, _event: str) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc


def _status(store: SqlSagaStore, event_id: int) -> str:
    with store.engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM pipeline_events WHERE id = :i"), {"i": event_id}
        ).first()
    return str(row[0]) if row else "gone"


async def _drain(controller: Any, store: SqlSagaStore, **kw: Any) -> int:
    return await run_drive_loop(controller, store, once=True, **kw)


# ---- the reported bug ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_handler_does_not_kill_the_loop(tmp_path: Path) -> None:
    """The bug itself: the exception escaped `run_drive_loop` and exited the process."""
    store = _store(tmp_path)
    event_id = store.enqueue("WMS-1", '{"kind": "start"}')

    handled = await _drain(_Controller(failures=1), store)

    assert handled == 0, "the failing event was not acked"
    assert _status(store, event_id) == "queued", "and it went back for another attempt"


@pytest.mark.asyncio
async def test_the_reported_scenario_recovers(tmp_path: Path) -> None:
    """A ConnectError on the loopback call to serve, exactly as the proxy produced — then the next
    attempt succeeds, which is what a transient failure should cost."""
    store = _store(tmp_path)
    store.enqueue("WMS-1", '{"kind": "start"}')
    controller = _Controller(failures=1, exc=httpx.ConnectError("All connection attempts failed"))

    await _drain(controller, store)  # fails, released
    handled = await _drain(controller, store)  # retried, succeeds

    assert handled == 1
    assert controller.calls == 2


@pytest.mark.asyncio
async def test_a_stranded_event_is_no_longer_unreachable(tmp_path: Path) -> None:
    """The heart of it. An event left `claimed` by a worker that died was invisible to `claim()`
    forever; a restarted orchestrator polled straight past it."""
    store = _store(tmp_path)
    event_id = store.enqueue("WMS-1", '{"kind": "start"}')
    store.claim("worker-that-then-died")
    assert _status(store, event_id) == "claimed"

    # A fresh worker, once the claim has gone stale.
    reclaimed = store.claim("worker-2", visibility_timeout=0.0)
    assert reclaimed is not None
    assert reclaimed[0] == event_id


def test_a_fresh_claim_is_not_stolen(tmp_path: Path) -> None:
    """The other side: a worker that is merely working must keep its event."""
    store = _store(tmp_path)
    store.enqueue("WMS-1", '{"kind": "start"}')
    store.claim("worker-1")

    assert store.claim("worker-2") is None, "a live claim must not be reclaimable"


def test_the_heartbeat_keeps_a_long_handler_alive(tmp_path: Path) -> None:
    """A stage can outlast any timeout worth setting, so the claim is refreshed rather than the
    timeout guessed high. Without this, a slow-but-healthy stage gets its event stolen."""
    store = _store(tmp_path)
    store.enqueue("WMS-1", '{"kind": "start"}')
    event_id = store.claim("worker-1")[0]  # type: ignore[index]

    # Stale enough to steal...
    assert store.claim("thief", visibility_timeout=0.0) is not None
    store.heartbeat(event_id)
    # ...but a heartbeat re-establishes it against a real timeout.
    assert store.claim("thief", visibility_timeout=300.0) is None


# ---- bounded retry, and a visible end ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_deterministic_failure_is_dead_lettered_not_retried_forever(
    tmp_path: Path,
) -> None:
    """Unbounded retry is not acceptable: this loop drives real work at real cost."""
    store = _store(tmp_path)
    event_id = store.enqueue("WMS-1", '{"kind": "start"}')
    controller = _Controller(failures=99)

    for _ in range(_DEFAULT_MAX_ATTEMPTS + 2):
        await _drain(controller, store)

    assert _status(store, event_id) == "failed"
    assert controller.calls == _DEFAULT_MAX_ATTEMPTS, "attempts are bounded"


@pytest.mark.asyncio
async def test_a_dead_lettered_event_says_why(tmp_path: Path) -> None:
    """The silence is what made the outage take hours. An event that has given up must say so."""
    store = _store(tmp_path)
    store.enqueue("WMS-1", '{"kind": "start"}')
    controller = _Controller(failures=99, exc=httpx.ConnectError("All connection attempts failed"))

    for _ in range(_DEFAULT_MAX_ATTEMPTS + 1):
        await _drain(controller, store)

    failed = store.failed_events()
    assert len(failed) == 1
    assert failed[0]["correlation_id"] == "WMS-1"
    assert "ConnectError" in failed[0]["last_error"]
    assert failed[0]["attempts"] == _DEFAULT_MAX_ATTEMPTS


def test_a_crash_loop_is_bounded_by_the_same_counter(tmp_path: Path) -> None:
    """A worker that is SIGKILLed never runs an `except` block, so reclaim is the only recovery —
    and it must not become an infinite loop of reclaiming and dying. `claim` increments the same
    counter, so the two paths share one bound."""
    store = _store(tmp_path)
    event_id = store.enqueue("WMS-1", '{"kind": "start"}')

    for _ in range(3):
        store.claim("worker", visibility_timeout=0.0)  # claim, then "die"

    assert store.attempts(event_id) == 3


@pytest.mark.asyncio
async def test_a_successful_event_is_unchanged(tmp_path: Path) -> None:
    """The guard: ordinary events must behave exactly as before."""
    store = _store(tmp_path)
    event_id = store.enqueue("WMS-1", '{"kind": "start"}')

    handled = await _drain(_Controller(), store)

    assert handled == 1
    assert _status(store, event_id) == "done"


# ---- recovery is not hand-written SQL ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dead_lettered_event_can_be_re_queued(tmp_path: Path) -> None:
    """Recovery took direct SQL against `pipeline_events`, because re-emitting is refused for an
    active saga and there is no gate to clear. `release` is that route back."""
    store = _store(tmp_path)
    event_id = store.enqueue("WMS-1", '{"kind": "start"}')
    controller = _Controller(failures=_DEFAULT_MAX_ATTEMPTS)

    for _ in range(_DEFAULT_MAX_ATTEMPTS + 1):
        await _drain(controller, store)
    assert _status(store, event_id) == "failed"

    store.release(event_id, "")
    assert _status(store, event_id) == "queued"
    assert await _drain(controller, store) == 1, "and it runs on the next poll"


# ---- the schema upgrades in place ----------------------------------------------------------------


def test_the_migration_adds_columns_to_a_pre_existing_table(tmp_path: Path) -> None:
    """`create_all` does not ALTER an existing table, so without a migration an upgraded deployment
    fails on its next claim with "no such column"."""
    db = tmp_path / "old.sqlite"
    engine = make_engine(f"sqlite:///{db}")
    with engine.begin() as conn:  # the pre-1.145 shape
        conn.execute(
            text(
                "CREATE TABLE pipeline_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "correlation_id TEXT NOT NULL, event TEXT NOT NULL, status TEXT NOT NULL, "
                "claimed_by TEXT, created_at TEXT NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO pipeline_events (correlation_id, event, status, created_at) "
                "VALUES ('WMS-1', '{}', 'queued', '2026-08-04T00:00:00Z')"
            )
        )
    engine.dispose()

    store = SqlSagaStore(make_engine(f"sqlite:///{db}"))
    claimed = store.claim("worker-1")

    assert claimed is not None, "the pre-existing row must still be claimable after upgrade"
    assert store.attempts(claimed[0]) == 1


def test_the_migration_is_idempotent(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    SqlSagaStore(make_engine(url))
    SqlSagaStore(make_engine(url))  # must not raise "duplicate column name"


# ---- the trigger ---------------------------------------------------------------------------------


def test_the_loopback_client_ignores_ambient_proxy_settings() -> None:
    """httpx honours HTTP_PROXY even for 127.0.0.1. Routing this control-plane call through an
    ambient proxy is never what an operator wants — and a WSL `autoProxy` pointing at a dead port
    is what killed the orchestrator."""
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/cli/_cmd_orchestrator.py"
    ).read_text()
    assert "trust_env=False" in src


def test_workers_are_distinguishable() -> None:
    """The default was the literal `orchestrator-1` for every process, so two orchestrators on one
    store were indistinguishable in `claimed_by`. That matters more now a stale claim is
    reclaimable: "whose claim is this" has to be answerable from the data."""
    name = default_worker_name()
    assert name != "orchestrator-1"
    assert str(__import__("os").getpid()) in name
