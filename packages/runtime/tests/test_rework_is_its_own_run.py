"""A reworked stage is a separate run, with its own id, row, cost and trace.

Reported as "the rework doesn't show up in Jobs — is it because the new job has the same id?" It
was, and the missing row was the least of it.

`stage_run_id` was `<correlation>:<stage>` with no attempt, so a rework — a human requests changes
and the stage runs again — reused the id. Measured on the store, before the fix::

    round 1: created=10:00  cost=$1.00  output='v1 draft'
    create_job -> IntegrityError: UNIQUE constraint failed: jobs.id   (swallowed, best-effort)
    final row: input='round 1'  output='v2 draft'  created_at=round 1's  cost=$1.50

Three things went wrong at once. The rework's INSERT failed on the primary key and was swallowed,
so it left no record. The closing UPDATE then succeeded against the FIRST row, producing one
chimera — round 1's input and start time beside round 2's output — whose elapsed time spans the
human's review and whose cost is round 2's alone, round 1's spend silently gone. And the trace
saves to ``{run_id}.json``, so the rework overwrote the trace of the draft the reviewer had
objected to: the one a reader most wants when asking why a change was requested.

`run_usage` rows are keyed by job id but appended, so `/usage` had both rounds' cost while the job
row showed one. The two views disagreed and neither said so.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from swarmkit_runtime.persistence import storage_for_workspace
from swarmkit_runtime.server._pipeline_stage import stage_run_id


def _store() -> Any:
    ws = Path(tempfile.mkdtemp()) / "ws"
    (ws / ".swarmkit").mkdir(parents=True)
    return storage_for_workspace(ws).store()


# ---- the id carries the attempt ---------------------------------------------------------------


def test_the_first_attempt_is_unsuffixed() -> None:
    """Existing rows, traces and links keep resolving — a fix that renamed every historical run id
    would trade one broken lookup for thousands."""
    assert stage_run_id("WMS-5", "design") == "WMS-5:design"
    assert stage_run_id("WMS-5", "design", 1) == "WMS-5:design"


def test_a_rework_gets_its_own_id() -> None:
    assert stage_run_id("WMS-5", "design", 2) == "WMS-5:design@2"
    assert stage_run_id("WMS-5", "design", 3) == "WMS-5:design@3"


def test_the_run_is_still_findable_from_its_pipeline() -> None:
    """The correlation prefix survives, so everything a run produced still groups together."""
    assert stage_run_id("WMS-5", "design", 2).startswith("WMS-5:")


def test_the_id_is_safe_in_a_path_and_a_filename() -> None:
    """It becomes `{run_id}.json` on disk and a path segment in
    `/observability/runs/{run_id}/trace`. `#` would have been read as a URL fragment."""
    run_id = stage_run_id("WMS-5", "design", 2)

    assert "#" not in run_id
    assert "/" not in run_id
    assert run_id == "WMS-5:design@2"


# ---- what that fixes in the store -------------------------------------------------------------


def test_both_attempts_are_recorded() -> None:
    """The reported symptom: the rework left no row at all, because its INSERT collided and the
    failure was swallowed as best-effort."""
    store = _store()

    for attempt in (1, 2):
        run_id = stage_run_id("WMS-5", "design", attempt)
        store.create_job(run_id, "design-swarm", f"round {attempt}", "WMS-5", "pipeline")
        store.update_job(run_id, status="completed", output=f"v{attempt} draft")

    assert {j.id for j in store.list_jobs()} == {"WMS-5:design", "WMS-5:design@2"}


def test_neither_attempt_is_a_chimera() -> None:
    """The worse half. The rework's UPDATE used to land on round 1's row, so one record carried
    round 1's input beside round 2's output — and nothing said it was two runs."""
    store = _store()
    for attempt, text in ((1, "v1 draft"), (2, "v2 draft")):
        run_id = stage_run_id("WMS-5", "design", attempt)
        store.create_job(run_id, "design-swarm", f"round {attempt}", "WMS-5", "pipeline")
        store.update_job(run_id, status="completed", output=text)

    rows = {j.id: j for j in store.list_jobs()}

    assert rows["WMS-5:design"].input == "round 1"
    assert rows["WMS-5:design"].output == "v1 draft"
    assert rows["WMS-5:design@2"].input == "round 2"
    assert rows["WMS-5:design@2"].output == "v2 draft"


def test_the_first_attempts_cost_survives_the_rework() -> None:
    """A pipeline's real spend was under-reported by every rework round: the second UPDATE
    overwrote the first attempt's cost rather than adding to it."""
    store = _store()
    for attempt, cost in ((1, 1.00), (2, 1.50)):
        run_id = stage_run_id("WMS-5", "design", attempt)
        store.create_job(run_id, "design-swarm", "in", "WMS-5", "pipeline")
        store.update_job(run_id, status="completed", usage_cost_usd=cost)

    total = sum(j.usage_cost_usd or 0 for j in store.list_jobs())

    assert total == 2.50, "a reworked stage cost both attempts, and the record must say so"


def test_every_attempt_belongs_to_the_run() -> None:
    """`/jobs/history?correlation_id=` must return a stage's whole story, not its last round."""
    store = _store()
    for attempt in (1, 2, 3):
        run_id = stage_run_id("WMS-5", "design", attempt)
        store.create_job(run_id, "design-swarm", "in", "WMS-5", "pipeline")

    assert len(store.list_jobs(correlation_id="WMS-5")) == 3


def test_two_pipelines_reworking_the_same_stage_do_not_collide() -> None:
    store = _store()
    for cid in ("WMS-5", "WMS-6"):
        for attempt in (1, 2):
            store.create_job(
                stage_run_id(cid, "design", attempt), "design-swarm", "in", cid, "pipeline"
            )

    assert len(store.list_jobs()) == 4
    assert len(store.list_jobs(correlation_id="WMS-6")) == 2


# ---- the traces stop overwriting each other ---------------------------------------------------


def test_each_attempt_writes_its_own_trace_file() -> None:
    """A trace saves to `{run_id}.json`. Sharing the id destroyed the trace of the draft the
    reviewer objected to — precisely the one someone asking "why was this reworked?" wants."""
    names = {f"{stage_run_id('WMS-5', 'design', n)}.json" for n in (1, 2, 3)}

    assert len(names) == 3
