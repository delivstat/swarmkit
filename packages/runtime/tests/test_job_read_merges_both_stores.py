"""A field the durable row can answer is never reported absent because a lighter object answered.

Bug 27 made a harness diff persist correctly. Reading it back still said there was none:

    GET  /jobs/a46614b158a7        -> diff_length: null
    GET  /jobs/a46614b158a7/diff   -> 404 "recorded no harness diff"
    psql> select length(diff) ...  -> 20997

Both endpoints resolved `await job_store.get(id) or _durable_job(...)` — in-memory first. The
in-process `Job` carries what changes during a run and nothing else; `diffs` is not one of its
fields, so `getattr(job, "diffs", None)` returned None and the 404 asserted the opposite of the
stored row. The durable fallback exists for jobs from another process or before a restart, and for
a job THIS process started it never misses — so it was unreachable in exactly the case that matters.

The fix is the general invariant rather than a special case for diffs. `labels`, `source`,
`correlation_id` and the usage fields were already in the same position, and any column added to
`JobRow` later would join them. `_JobView` merges: the live object answers for the fields it has,
because those change while the run is in flight, and the durable row supplies everything else.

`test_every_durable_field_is_reachable_through_the_view` is the guard — it is the test whose absence
let a correctly-written diff read as missing.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from swarmkit_runtime.persistence._store import JobRow, Store, make_engine
from swarmkit_runtime.server._jobs import Job
from swarmkit_runtime.server._routes_jobs import _diff_length, _resolve_job, _to_response

DIFF = "--- a/x\n+++ b/x\n@@ -1 +1,2 @@\n a\n+b\n"


def _live(**over: Any) -> Job:
    base: dict[str, Any] = {
        "id": "job-1",
        "topology": "wms-develop",
        "status": "completed",
        "input": "edit the file",
    }
    base.update(over)
    return Job(**base)


def _row(**over: Any) -> JobRow:
    base: dict[str, Any] = {
        "id": "job-1",
        "topology": "wms-develop",
        "status": "completed",
        "input": "edit the file",
        "diffs": {"builder": DIFF},
        "correlation_id": "WMS-35",
        "source": "serve",
        "labels": {"map": "WMS-35"},
    }
    base.update(over)
    return JobRow(**base)


# ---- the reported failure ------------------------------------------------------------------------


def test_a_persisted_diff_is_not_reported_absent() -> None:
    """The reproduction: in-memory-first made a stored 20,997-character diff read as None."""
    view = _resolve_job(_live(), _row())

    assert view.diffs == {"builder": DIFF}
    assert _diff_length(view) == len(DIFF)


def test_the_diff_endpoint_would_no_longer_404() -> None:
    """`diffs is None` is what raised the 404 that contradicted the stored row."""
    assert getattr(_resolve_job(_live(), _row()), "diffs", None) is not None


def test_the_live_object_alone_still_cannot_answer() -> None:
    """Stated so the fix is not mistaken for the `Job` dataclass having grown a field."""
    assert _diff_length(_live()) is None


# ---- the general invariant -----------------------------------------------------------------------


def test_every_durable_field_is_reachable_through_the_view() -> None:
    """The guard whose absence caused this.

    Any column `JobRow` gains must be answerable when both stores hold the job. `diffs` was the
    field that broke; `labels`, `source` and `correlation_id` were already silently None for an
    in-process job, and the next column added would have joined them.
    """
    view = _resolve_job(_live(), _row())

    for f in dataclass_fields(JobRow):
        assert getattr(view, f.name, None) == getattr(_row(), f.name), (
            f"{f.name} is not reachable through the merged view"
        )


def test_the_live_object_wins_for_what_it_carries() -> None:
    """Liveness fields change while a run is in flight, so the in-memory object is the fresher
    answer for them — the reason precedence was not simply reversed."""
    view = _resolve_job(
        _live(status="running", events=["step 1", "step 2"]),
        _row(status="completed", events=[]),
    )

    assert view.status == "running"
    assert view.events == ["step 1", "step 2"]


# ---- and the degenerate cases still work ---------------------------------------------------------


def test_only_a_durable_row_resolves_to_it() -> None:
    """A CLI run, another process, or anything from before a restart."""
    assert _resolve_job(None, _row()).diffs == {"builder": DIFF}


def test_only_a_live_job_resolves_to_it() -> None:
    """No durable store — the job is still openable, just without persisted-only fields."""
    assert _resolve_job(_live(), None).status == "completed"


def test_neither_resolves_to_none() -> None:
    assert _resolve_job(None, None) is None


def test_the_response_carries_the_merged_fields() -> None:
    """End of the chain: what `GET /jobs/{id}` actually returns."""
    response = _to_response(_resolve_job(_live(), _row()))

    assert response.diff_length == len(DIFF)
    assert response.job_id == "job-1"


def test_a_run_that_changed_nothing_is_still_zero_not_none(tmp_path: Path) -> None:
    """The distinction bug 27 exists to preserve, asserted through the merged read."""
    store = Store(make_engine(f"sqlite:///{tmp_path / 's.sqlite'}"))
    store.create_job("job-1", "t", "i")
    store.update_job("job-1", diffs={})

    assert _diff_length(_resolve_job(_live(), store.get_job("job-1"))) == 0


# ---- the runtime's own file is not the agent's work ----------------------------------------------


def test_the_mcp_config_is_excluded_from_the_collected_diff() -> None:
    """Every gated harness diff opened with `diff --git a/.swarmkit-mcp.json`.

    `delivered` covers context files; the gateway config is written into the sandbox root by the
    runtime and was not excluded — so the runtime's own bearer token was presented as the agent's
    first authored change, in the artifact a human approves.
    """
    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "collect_diff(sandbox, [*delivered, *_RUNTIME_WRITTEN])" in src
    assert '_MCP_CONFIG_NAME = ".swarmkit-mcp.json"' in src
    # The written path and the excluded name must be the same constant, or they drift apart again.
    assert "Path(sandbox.root) / _MCP_CONFIG_NAME" in src
