"""A harness agent's diff survives the run and can be read back.

A `wms-develop` run edited 11 files across 850 seconds and a few dollars, returned
`status: completed`, and produced nothing retrievable. The worktree is torn down in
`worktree_sandbox`'s `finally`, so once the run ended the code was gone.

Everything upstream was correct. The sandbox was provisioned, `collect_diff` handled new files,
and `_harness_node` set `result["diff"]` with a comment stating the intent. **`diff` was never a
`SwarmState` key**, so it never entered graph state and never reached `RunResult` — the funnel's
deterministic validate layers saw it only because they read the node's return dict through a
closure. It died at the boundary between the node and everything that persists.

The dangerous part is not the loss, it is that the loss was invisible: a run whose work was
dropped looked exactly like a run that changed nothing. So `diffs` is `None` when no diff was
carried out of a run and `{}` when a harness ran and changed nothing, and every surface keeps
those apart:

* `GET /jobs/{id}` carries `diff_length` — `None` vs `0` — but not the content, which can be
  megabytes on a fetch the UI makes for every run;
* `GET /jobs/{id}/diff` carries the content, and 404s when the run recorded none.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from swarmkit_runtime._workspace_runtime import RunResult
from swarmkit_runtime.persistence._store import Store, make_engine

DIFF = """--- a/app.properties
+++ b/app.properties
@@ -1,3 +1,4 @@
 existing=1
+added=2
"""


def _store(tmp_path: Path) -> Store:
    return Store(make_engine(f"sqlite:///{tmp_path / 'store.sqlite'}"))


# ---- the state key that was missing -------------------------------------------------------------


def test_diffs_is_a_declared_state_key() -> None:
    """The root cause. `result["diff"]` was set on the node result and `diff` was not in the
    schema, so LangGraph never carried it into the graph result."""
    from swarmkit_runtime.langgraph_compiler._state import SwarmState  # noqa: PLC0415

    assert "diffs" in SwarmState.__annotations__


def test_the_harness_node_writes_the_state_key() -> None:
    """Asserted against the source: the node has to populate the key, not just the result dict the
    funnel reads through its closure."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert 'result["diffs"] = {agent_id: diff}' in src


def test_diffs_are_keyed_by_agent() -> None:
    """Not a single last-write-wins string: two harness agents in one run would lose one's work."""
    result = RunResult(output="", diffs={"builder": DIFF, "fixer": "--- b"})

    assert set(result.diffs) == {"builder", "fixer"}
    assert DIFF in result.diff
    assert "--- b" in result.diff


# ---- it reaches the record ----------------------------------------------------------------------


def test_a_job_records_its_diff(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job("job-1", "wms-develop", "edit the file")
    store.update_job("job-1", diffs={"builder": DIFF})

    assert store.list_jobs()[0].diffs == {"builder": DIFF}


def test_the_diff_round_trips_as_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job("job-1", "t", "i")
    store.update_job("job-1", diffs={"builder": DIFF})

    with store.engine.connect() as conn:
        from sqlalchemy import text  # noqa: PLC0415

        raw = conn.execute(text("select diff from jobs")).scalar_one()

    assert json.loads(raw)["builder"] == DIFF


def test_no_diff_is_none_not_empty(tmp_path: Path) -> None:
    """The distinction that makes the loss detectable. NULL means no diff was carried out of the
    run; an empty dict means a harness ran and changed nothing."""
    store = _store(tmp_path)
    store.create_job("job-1", "t", "i")
    store.update_job("job-1", status="completed")

    assert store.list_jobs()[0].diffs is None


def test_a_harness_that_changed_nothing_records_an_empty_dict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_job("job-1", "t", "i")
    store.update_job("job-1", diffs={})

    assert store.list_jobs()[0].diffs == {}


def test_the_column_is_added_to_an_existing_table(tmp_path: Path) -> None:
    """`create_all` does not alter an existing table, so an upgraded deployment would fail on its
    next insert with "no such column"."""
    from sqlalchemy import inspect, text  # noqa: PLC0415

    engine = make_engine(f"sqlite:///{tmp_path / 'old.sqlite'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, topology TEXT NOT NULL, "
                "status TEXT NOT NULL, input TEXT NOT NULL, created_at TEXT NOT NULL, "
                "events TEXT, version TEXT, output TEXT, error TEXT, completed_at TEXT, "
                "usage_input_tokens INTEGER, usage_output_tokens INTEGER, usage_cost_usd FLOAT)"
            )
        )

    store = Store(engine)

    assert "diff" in {c["name"] for c in inspect(engine).get_columns("jobs")}
    store.create_job("job-1", "t", "i")
    store.update_job("job-1", diffs={"builder": DIFF})
    assert store.list_jobs()[0].diffs == {"builder": DIFF}


# ---- every run path records it ------------------------------------------------------------------


def test_every_run_path_passes_the_diff_through() -> None:
    """Three writers — serve, cli, chat — and a diff recorded by two of them is a diff lost on the
    third. Asserted as a property against the source, the way the `source` field is.

    It was four until the bundled pipeline was removed; the stage writer left with it.
    """
    root = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"
    expected = {
        "cli/_cmd_run.py": "diffs=result.diffs",
        "_conversation.py": 'diffs=getattr(result, "diffs", {}) or {}',
        "server/_jobs.py": 'fields["diffs"]',
    }

    for rel, needle in expected.items():
        assert needle in (root / rel).read_text(), f"{rel} does not record the harness diff"


# ---- and it is readable back ---------------------------------------------------------------------


def test_the_job_response_carries_the_length_not_the_content() -> None:
    """A diff can be megabytes and the UI fetches the job for every run — so the length rides on
    the job and the content has its own endpoint."""
    from swarmkit_runtime.server._routes_jobs import _diff_length  # noqa: PLC0415
    from swarmkit_runtime.server._schemas import JobResponse  # noqa: PLC0415

    assert "diff_length" in JobResponse.model_fields
    assert "diff" not in JobResponse.model_fields

    class _Job:
        diffs: ClassVar[dict[str, str]] = {"builder": DIFF}

    assert _diff_length(_Job()) == len(DIFF)


def test_the_length_distinguishes_nothing_changed_from_nothing_recorded() -> None:
    """The whole safety property, in one assertion."""
    from swarmkit_runtime.server._routes_jobs import _diff_length  # noqa: PLC0415

    class _Dropped:
        diffs = None

    class _Clean:
        diffs: ClassVar[dict[str, str]] = {}

    assert _diff_length(_Dropped()) is None
    assert _diff_length(_Clean()) == 0


@pytest.mark.asyncio
async def test_the_diff_endpoint_is_registered() -> None:
    """`GET /jobs/{id}/diff` — the retrieval path the report asks for."""
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_routes_jobs.py"
    ).read_text()

    assert '@app.get("/jobs/{job_id}/diff")' in src
    # 404s when the run recorded none, rather than returning an empty diff that reads as "clean".
    assert "recorded no harness diff" in src


def test_run_result_exposes_a_combined_diff() -> None:
    """What a caller wants to read or pipe to `git apply`."""
    assert RunResult(output="", diffs={}).diff == ""
    assert RunResult(output="", diffs={"a": DIFF}).diff == DIFF


def test_job_row_defaults_to_none(tmp_path: Path) -> None:
    """A run that never touched a harness must not claim an empty diff."""
    from swarmkit_runtime.persistence._store import JobRow  # noqa: PLC0415

    assert JobRow(id="j", topology="t", status="completed", input="i").diffs is None
