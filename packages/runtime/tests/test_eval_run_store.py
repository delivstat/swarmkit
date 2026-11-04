"""`eval_runs` on the store (design/details/eval-on-serve.md §3, §4).

Every test runs on SQLite, and again on Postgres when SWARMKIT_TEST_POSTGRES_URL is set — the same
gate the job store's dialect test uses.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from swarmkit_runtime.persistence import SqliteStore, Store
from swarmkit_runtime.persistence._store import make_engine
from swarmkit_runtime.persistence._tables import eval_runs


@pytest.fixture(params=["sqlite", "postgres"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Store]:
    if request.param == "sqlite":
        yield SqliteStore(tmp_path)
        return
    url = os.environ.get("SWARMKIT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set SWARMKIT_TEST_POSTGRES_URL to run the Postgres eval-run tests")
    yield Store(make_engine(url))


def _new(store: Store, set_id: str | None = None, total: int = 3) -> str:
    run_id = str(uuid.uuid4())
    store.create_eval_run(
        run_id,
        eval_set_id=set_id or f"set-{uuid.uuid4().hex[:8]}",
        target="hello",
        source="cli",
        eval_set={"metadata": {"id": "s"}, "cases": [{"id": f"c{i}"} for i in range(total)]},
        total=total,
    )
    return run_id


def _case(case_id: str, passed: bool = True) -> dict[str, object]:
    return {"case_id": case_id, "passed": passed, "checks": [], "output": "", "error": None}


def _age_heartbeat(store: Store, run_id: str, seconds: float) -> None:
    old = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    with store.engine.begin() as conn:
        conn.execute(update(eval_runs).where(eval_runs.c.id == run_id).values(heartbeat_at=old))


def test_a_run_round_trips_with_its_snapshot(store: Store) -> None:
    run_id = _new(store, total=2)
    row = store.get_eval_run(run_id)
    assert row is not None
    assert row.status == "running" and row.source == "cli" and row.total == 2
    assert [c["id"] for c in row.eval_set["cases"]] == ["c0", "c1"]
    assert row.cases == [] and row.finished_at is None


def test_cases_append_in_order_with_passes_and_cost_summed(store: Store) -> None:
    run_id = _new(store)
    before = store.get_eval_run(run_id).heartbeat_at  # type: ignore[union-attr]
    store.record_eval_case(run_id, _case("c0"), 0.25)
    store.record_eval_case(run_id, _case("c1", passed=False), 0.5)
    row = store.get_eval_run(run_id)
    assert row is not None
    assert [c["case_id"] for c in row.cases] == ["c0", "c1"]
    assert row.passed == 1
    assert row.cost_usd == pytest.approx(0.75)
    assert row.heartbeat_at >= before
    assert row.recorded_case_ids == {"c0", "c1"}


def test_finish_writes_status_report_and_diff(store: Store) -> None:
    run_id = _new(store, total=1)
    store.finish_eval_run(
        run_id, "succeeded", report={"passed": 1, "cases": [_case("c0")]}, diff={"new": ["c0"]}
    )
    row = store.get_eval_run(run_id)
    assert row is not None
    assert row.status == "succeeded" and row.finished_at is not None
    assert row.passed == 1 and row.diff == {"new": ["c0"]}


def test_listing_is_newest_first_and_filters_by_set(store: Store) -> None:
    set_id = f"set-{uuid.uuid4().hex[:8]}"
    first, second = _new(store, set_id), _new(store, set_id)
    _new(store)  # another set
    ids = [r.id for r in store.list_eval_runs(set_id)]
    assert ids == [second, first]


def test_the_prior_run_is_the_newest_finished_one_never_in_flight(store: Store) -> None:
    set_id = f"set-{uuid.uuid4().hex[:8]}"
    done = _new(store, set_id)
    store.finish_eval_run(done, "failed", report={"passed": 0, "cases": []})
    stopped = _new(store, set_id)
    store.finish_eval_run(stopped, "stopped")
    in_flight = _new(store, set_id)
    prior = store.latest_finished_eval_run(set_id, exclude_id=in_flight)
    assert prior is not None and prior.id == done
    # A run that finished after this one started is not its prior.
    assert store.latest_finished_eval_run(set_id, finished_before="2000-01-01") is None


def test_the_sweep_takes_stale_heartbeats_and_leaves_fresh_ones(store: Store) -> None:
    """Two processes share one store: only the row nobody is moving gets swept."""
    stale, fresh = _new(store), _new(store)
    _age_heartbeat(store, stale, 3600)
    swept = store.sweep_stale_eval_runs(300, "gone")
    assert swept >= 1
    assert store.get_eval_run(stale).status == "interrupted"  # type: ignore[union-attr]
    assert store.get_eval_run(stale).error == "gone"  # type: ignore[union-attr]
    assert store.get_eval_run(fresh).status == "running"  # type: ignore[union-attr]


def test_a_heartbeat_keeps_a_long_case_from_being_swept(store: Store) -> None:
    run_id = _new(store)
    _age_heartbeat(store, run_id, 3600)
    store.touch_eval_run(run_id)
    store.sweep_stale_eval_runs(300, "gone")
    assert store.get_eval_run(run_id).status == "running"  # type: ignore[union-attr]


def test_resume_is_claimed_once_and_only_from_a_resumable_state(store: Store) -> None:
    run_id = _new(store)
    assert not store.claim_eval_resume(run_id), "a running eval cannot be resumed"
    store.finish_eval_run(run_id, "interrupted", error="gone")
    assert store.claim_eval_resume(run_id)
    assert not store.claim_eval_resume(run_id), "a second resumer must lose"
    row = store.get_eval_run(run_id)
    assert row is not None and row.status == "running"
    assert row.finished_at is None and row.error is None

    done = _new(store)
    store.finish_eval_run(done, "succeeded")
    assert not store.claim_eval_resume(done)


def test_a_stop_is_recorded_only_on_a_running_eval(store: Store) -> None:
    run_id = _new(store)
    assert not store.eval_stop_requested(run_id)
    assert store.request_eval_stop(run_id)
    assert store.eval_stop_requested(run_id)
    store.finish_eval_run(run_id, "stopped")
    assert not store.request_eval_stop(run_id)
    # Resuming clears the request, so the resumed run is not stopped at once.
    assert store.claim_eval_resume(run_id)
    assert not store.eval_stop_requested(run_id)


def test_storage_migrate_copies_eval_runs() -> None:
    """Left out of the explicit list, a SQLite→Postgres move would drop eval history silently."""
    from swarmkit_runtime.cli._cmd_storage import _TABLES  # noqa: PLC0415

    assert "eval_runs" in _TABLES["store.sqlite"]
