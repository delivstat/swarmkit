"""Fenced jobs-row writes (worker-execution.md): update_job(fence_worker_id=...) applies only while
the naming worker still owns the run. This is what stops a zombie worker (lease expired, run
reclaimed) from clobbering the new owner's durable row on its intermediate/terminal writes.

SQLite is enough — the fence is a plain WHERE clause, not a Postgres-specific mechanism.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import update
from swarmkit_runtime.persistence._store import Store, make_engine
from swarmkit_runtime.persistence._tables import jobs


def _store(tmp_path: Path) -> Store:
    return Store(make_engine(f"sqlite:///{tmp_path / 's.sqlite'}"))


def test_update_job_is_fenced_on_worker_ownership(tmp_path: Path) -> None:
    st = _store(tmp_path)
    st.create_job("j", "t", "x")
    # A worker "B" owns the run (as a claim would set it).
    with st._engine.begin() as c:
        c.execute(update(jobs).where(jobs.c.id == "j").values(worker_id="B", status="running"))

    # Zombie "A" — its lease expired and B reclaimed — cannot write: the fence matches no row.
    assert st.update_job("j", status="completed", output="A", fence_worker_id="A") is False
    row = st.get_job("j")
    assert row is not None and row.status == "running" and row.output is None

    # The real owner "B" writes cleanly.
    assert st.update_job("j", status="completed", output="B", fence_worker_id="B") is True
    row = st.get_job("j")
    assert row is not None and row.status == "completed" and row.output == "B"


def test_unfenced_update_is_unconditional(tmp_path: Path) -> None:
    """All-in-one serve passes no token (there is no owner), so the write applies as before."""
    st = _store(tmp_path)
    st.create_job("j", "t", "x")
    with st._engine.begin() as c:
        c.execute(update(jobs).where(jobs.c.id == "j").values(worker_id="B"))
    assert st.update_job("j", status="completed", fence_worker_id=None) is True
    row = st.get_job("j")
    assert row is not None and row.status == "completed"
