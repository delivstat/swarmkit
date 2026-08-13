"""The reference orchestrator drives SwarmKit without importing it.

`design/details/extracting-the-pipeline.md` calls this the acceptance test for the whole extraction:
if an application cannot sequence runs over the public API, the boundary is in the wrong place.

The fake below is the API contract written down — every response shape the orchestrator relies on.
It is deliberately a fake rather than a live server: what is under test is the *sequencing*, and a
fake makes "it waited for a human, then resumed" assertable without a real human or a real wait.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import (
    Run,
    Stage,
    StageFailed,
    StageRejected,
    run_pipeline,
    run_stage,
    thread_upstream,
)


class FakeServe:
    """`swarmkit serve`, scripted. Each job's statuses are consumed in order as it is polled."""

    def __init__(self, script: dict[str, list[str]], **over: Any) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []
        self.resumed: list[str] = []
        self.started: list[tuple[str, dict[str, Any]]] = []
        self.gate_status = over.get("gate_status", "approved")
        self.review = over.get("review", [{"run_id": "job-1", "gate_id": "job-1:designer"}])
        self.outputs = over.get("outputs", {})
        self.diffs = over.get("diffs", {})
        self._next_job = 0

    def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        self.calls.append(f"POST {path}")
        if path.startswith("/run/"):
            self._next_job += 1
            job_id = f"job-{self._next_job}"
            self.started.append((path.removeprefix("/run/"), dict(json or {})))
            return {"job_id": job_id, "status": "running"}
        if path.endswith("/resume"):
            self.resumed.append(path.split("/")[2])
            return {"job_id": path.split("/")[2], "status": "running"}
        raise AssertionError(f"unexpected POST {path}")

    def get(self, path: str, **_params: Any) -> Any:
        self.calls.append(f"GET {path}")
        if path == "/review":
            return self.review
        if path.startswith("/gates/"):
            return {"gate_id": path.removeprefix("/gates/"), "status": self.gate_status}
        if path.endswith("/diff"):
            job = path.split("/")[2]
            return {"diffs": self.diffs.get(job, {})}
        if path.startswith("/jobs/"):
            job = path.split("/")[2]
            status = self.script[job].pop(0) if self.script.get(job) else "completed"
            return {
                "job_id": job,
                "status": status,
                "output": self.outputs.get(job, f"{job} output"),
                "diff_length": len(str(self.diffs.get(job, ""))) if job in self.diffs else None,
            }
        raise AssertionError(f"unexpected GET {path}")


def _noop(_seconds: float) -> None:
    """No real waiting: the point is the sequence of calls, not the clock."""


def _stage(**over: Any) -> Stage:
    return Stage(**{"id": "design", "topology": "wms-design", **over})


# ---- the ordinary path ---------------------------------------------------------------------------


def test_a_stage_runs_and_returns_its_artifact() -> None:
    serve = FakeServe({"job-1": ["running", "completed"]})

    artifact = run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_noop)

    assert artifact == "job-1 output"
    assert serve.started == [("wms-design", {"input": "WMS-35", "correlation_id": "WMS-35"})]


def test_the_run_is_correlated() -> None:
    """Every stage carries the ticket, which is what makes the whole sequence one trail."""
    serve = FakeServe({"job-1": ["completed"]})

    run_pipeline(serve, "WMS-35", [_stage()], sleep=_noop)

    assert serve.started[0][1]["correlation_id"] == "WMS-35"


def test_artifacts_thread_into_the_next_stage() -> None:
    serve = FakeServe(
        {"job-1": ["completed"], "job-2": ["completed"]},
        outputs={"job-1": "the triage", "job-2": "the spec"},
    )

    run = run_pipeline(
        serve,
        "WMS-35",
        [Stage("triage", "wms-triage"), Stage("design", "wms-design", after=("triage",))],
        sleep=_noop,
    )

    assert run.artifacts == {"triage": "the triage", "design": "the spec"}
    assert serve.started[1][1]["input"] == "the triage"


def test_a_harness_stage_returns_its_diff() -> None:
    """The work product, not the summary — which is why bug 27 mattered."""
    serve = FakeServe({"job-1": ["completed"]}, diffs={"job-1": {"builder": "--- a/x"}})

    artifact = run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_noop)

    assert "--- a/x" in artifact


# ---- and the human in the middle -----------------------------------------------------------------


def test_a_deferred_stage_waits_for_the_gate_then_resumes() -> None:
    """The whole reason this is not a for-loop over POST /run."""
    serve = FakeServe({"job-1": ["deferred", "completed"]})

    artifact = run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_noop)

    assert serve.resumed == ["job-1"], "the run must be released after the gate approved"
    assert artifact == "job-1 output"


def test_it_keeps_waiting_while_the_gate_is_pending() -> None:
    """Days of pending is the correct behaviour for a gate, not a stall."""
    serve = FakeServe({"job-1": ["deferred", "completed"]})
    serve.gate_status = "pending"
    polls = {"n": 0}

    def _tick(_seconds: float) -> None:
        polls["n"] += 1
        if polls["n"] == 3:
            serve.gate_status = "approved"

    run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_tick)

    assert serve.resumed == ["job-1"]
    assert polls["n"] >= 3


def test_the_gate_verdict_comes_from_the_server() -> None:
    """`GET /gates/{id}` applies quorum, distinct approvers and exclude_author. Counting approved
    role-tasks here would be reimplementing an approval policy this app cannot see."""
    serve = FakeServe({"job-1": ["deferred", "completed"]})

    run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_noop)

    assert any(c.startswith("GET /gates/") for c in serve.calls)


def test_the_gate_is_found_by_run_not_guessed() -> None:
    """The gate id has two shapes; the review list carries `run_id`, so it is looked up."""
    serve = FakeServe(
        {"job-1": ["deferred", "completed"]},
        review=[
            {"run_id": "some-other-run", "gate_id": "other:agent"},
            {"run_id": "job-1", "gate_id": "job-1:designer"},
        ],
    )

    run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_noop)

    assert "GET /gates/job-1:designer" in serve.calls


def test_a_rejected_gate_stops_the_stage() -> None:
    serve = FakeServe({"job-1": ["deferred"]}, gate_status="rejected")

    with pytest.raises(StageRejected):
        run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_noop)

    assert serve.resumed == [], "a rejected run must not be released"


def test_a_failed_stage_raises_with_the_reason() -> None:
    """The application owns the retry policy — this reports, it does not decide."""
    serve = FakeServe({"job-1": ["failed"]})
    serve.outputs["job-1"] = ""

    with pytest.raises(StageFailed):
        run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_noop)


def test_a_deferral_with_no_gate_is_an_error_not_a_hang() -> None:
    """Parked on something this app cannot resolve: say so rather than polling forever."""
    serve = FakeServe({"job-1": ["deferred"]}, review=[])

    with pytest.raises(StageFailed):
        run_stage(serve, Run("WMS-35"), _stage(), build_input=thread_upstream, sleep=_noop)


# ---- the boundary itself -------------------------------------------------------------------------


def test_the_app_does_not_import_the_runtime() -> None:
    """The acceptance test for the extraction. If this app needed the runtime, sequencing would not
    actually be separable and the design note would be wrong."""
    root = Path(__file__).resolve().parents[1]

    for source in root.glob("*.py"):
        # IMPORTS, not mentions: the docstrings name the runtime to explain what this is not.
        for line in source.read_text().splitlines():
            stripped = line.strip()
            assert not stripped.startswith(("import swarmkit_runtime", "from swarmkit_runtime")), (
                f"{source.name} imports the runtime: {stripped}"
            )


def test_the_whole_integration_is_five_endpoints() -> None:
    """Stated as a test because it is the claim: an application needs this much SwarmKit and no
    more. A sixth appearing here is a signal the boundary moved."""
    client = (Path(__file__).resolve().parents[1] / "client.py").read_text()

    for endpoint in ("/run/", "/jobs/", "/review", "/gates/", "/resume"):
        assert endpoint in client
