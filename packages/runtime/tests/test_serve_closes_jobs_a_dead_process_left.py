"""A job left `running` by a dead process is closed, not left pretending to work.

A job started through `POST /run/{topology}` executes as a task in the serve process. When that
process dies the task dies with it, the in-memory store is empty on restart, and nothing reconciled
the durable row — so it sat at `running` for ever. In the UI that is indistinguishable from work
still in flight: the stalled shape, where a reader waits for something that will never finish.

Pipeline runs already recover themselves — a claim goes stale after the visibility timeout and a
restarted orchestrator reclaims the event. These never did.

Two decisions worth stating, because both could reasonably have gone the other way:

**Bounded by the job timeout, not by "everything running at startup".** Several instances can share
one Postgres store, so a blanket sweep would close another live instance's in-flight jobs — trading
a stuck row for a false one, which is worse. No job may exceed the timeout, so a row older than it
is not running whoever owns it.

**`interrupted`, not `failed`.** The run may well have finished its work before the process died.
What is known is that it was interrupted; claiming more is how a record starts lying in the other
direction.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import update
from swarmkit_runtime.persistence import storage_for_workspace
from swarmkit_runtime.persistence._tables import jobs


def _store() -> Any:
    ws = Path(tempfile.mkdtemp()) / "ws"
    (ws / ".swarmkit").mkdir(parents=True)
    return storage_for_workspace(ws).store()


def _aged(store: Any, job_id: str, *, hours: float, status: str = "running") -> None:
    """Backdate a row, standing in for a job a previous process started."""
    when = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with store.engine.begin() as conn:
        conn.execute(update(jobs).where(jobs.c.id == job_id).values(status=status, created_at=when))


REASON = "the server restarted while this run was in flight"


# ---- the stuck row is closed --------------------------------------------------------------------


def test_a_job_left_running_is_marked_interrupted() -> None:
    """The bug: the row said `running` for ever, and a reader could not tell it from live work."""
    store = _store()
    store.create_job("orphan", "hello", "in", None, "serve")
    _aged(store, "orphan", hours=2)

    swept = store.sweep_stale_jobs(3600, REASON)

    row = store.get_job("orphan")
    assert swept == 1
    assert row.status == "interrupted"
    assert row.error == REASON


def test_it_is_interrupted_rather_than_failed() -> None:
    """The run may have finished its work before the process died. `failed` asserts more than is
    known — the same overclaim in the opposite direction."""
    store = _store()
    store.create_job("orphan", "hello", "in", None, "serve")
    _aged(store, "orphan", hours=2)

    store.sweep_stale_jobs(3600, REASON)

    assert store.get_job("orphan").status == "interrupted"


def test_the_row_is_closed_so_it_stops_reading_as_in_flight() -> None:
    """`completed_at` is what distinguishes a finished row from a running one everywhere else."""
    store = _store()
    store.create_job("orphan", "hello", "in", None, "serve")
    _aged(store, "orphan", hours=2)

    store.sweep_stale_jobs(3600, REASON)

    assert store.get_job("orphan").completed_at


def test_a_pending_job_is_swept_too() -> None:
    """A job that never got as far as running is just as stuck."""
    store = _store()
    store.create_job("never-started", "hello", "in", None, "serve")
    _aged(store, "never-started", hours=2, status="pending")

    assert store.sweep_stale_jobs(3600, REASON) == 1


# ---- nothing live is touched --------------------------------------------------------------------


def test_a_recently_started_job_is_left_alone() -> None:
    """The multi-instance case. Several servers can share one Postgres store, so a blanket sweep
    would close another live instance's work — a false record, which is worse than a stuck one."""
    store = _store()
    store.create_job("live", "hello", "in", None, "serve")
    store.update_job("live", status="running")

    swept = store.sweep_stale_jobs(3600, REASON)

    assert swept == 0
    assert store.get_job("live").status == "running"


def test_a_finished_job_is_not_reopened() -> None:
    store = _store()
    store.create_job("done", "hello", "in", None, "serve")
    store.update_job("done", status="completed", completed_at="2026-08-07T10:00:00Z", output="ok")
    _aged(store, "done", hours=5, status="completed")

    store.sweep_stale_jobs(3600, REASON)

    row = store.get_job("done")
    assert row.status == "completed"
    assert row.output == "ok"


def test_an_already_interrupted_job_is_not_touched_again() -> None:
    store = _store()
    store.create_job("gone", "hello", "in", None, "serve")
    store.update_job("gone", status="interrupted", error="the original reason")
    _aged(store, "gone", hours=5, status="interrupted")

    store.sweep_stale_jobs(3600, REASON)

    assert store.get_job("gone").error == "the original reason"


def test_the_timeout_is_the_boundary() -> None:
    """Older than the timeout is swept; younger is not — stated as the rule, since the rule is the
    only thing making this safe across instances."""
    store = _store()
    store.create_job("just-inside", "hello", "in", None, "serve")
    _aged(store, "just-inside", hours=0.4)  # 24 min, inside a 1h timeout
    store.create_job("just-outside", "hello", "in", None, "serve")
    _aged(store, "just-outside", hours=1.4)  # 84 min, past it

    store.sweep_stale_jobs(3600, REASON)

    assert store.get_job("just-inside").status == "running"
    assert store.get_job("just-outside").status == "interrupted"


def test_an_empty_store_sweeps_nothing() -> None:
    assert _store().sweep_stale_jobs(3600, REASON) == 0


# ---- it runs at startup, and never blocks it ----------------------------------------------------


def test_serve_sweeps_on_startup() -> None:
    from pathlib import Path as P  # noqa: PLC0415

    src = (P(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_app.py").read_text()

    assert "_sweep_stale_jobs(app.state.store, cfg.timeout_seconds)" in src


def test_a_broken_store_does_not_stop_the_server_starting() -> None:
    """Losing the cleanup is acceptable; refusing to serve is not."""
    from swarmkit_runtime.server._app import _sweep_stale_jobs  # noqa: PLC0415

    class _Broken:
        def sweep_stale_jobs(self, *_a: Any, **_kw: Any) -> int:
            raise OSError("disk went away")

    _sweep_stale_jobs(_Broken(), 3600)
    _sweep_stale_jobs(None, 3600)
