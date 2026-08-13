"""A gate says which run produced what it is approving, and an artifact is fetchable over HTTP.

**The gate link.** The gates page split `gate_id` on the last colon and linked at `/runs` — the
pipeline saga view. That is one of the two shapes: a stage's gate is `<correlation>:<stage>`, but
an in-node funnel gate's is `<run>:<agent>`, so the split returned a *topology id* and the link
searched for a pipeline run that does not exist. Every gated topology run pointed at "No pipeline
runs to show". It was latent only while the in-node approve layer opened nothing.

The fix is not a better split. `open_gate` stamps `run_id` — always `jobs.id`, for both shapes — so
no surface has to infer structure from a string, and both kinds link to the same place, because
every gated run has a job row (a stage's since 1.152.0, a CLI run's since 1.150.0).

**The artifact endpoint.** The store has existed since the pipeline orchestrator shipped and only
stage code ever wrote to it; `swarmkit artifacts get` reads it from a terminal and nothing could
over HTTP. So an application sequencing its own runs could not thread a stage's output onward,
and a gate UI could not render the artifact it was asking someone to approve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.artifacts import build_artifact_store
from swarmkit_runtime.governance._approval import ApprovalPolicy
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import open_gate

POLICY = ApprovalPolicy.from_dict(
    {"rules": [{"scope": "design:approve", "roles": ["lead"], "quorum": "all"}]}
)


def _open(tmp_path: Path, gate_id: str, run_id: str) -> FileReviewQueue:
    queue = FileReviewQueue(tmp_path)
    open_gate(
        queue,
        gate_id=gate_id,
        topology_id="wms-design",
        agent_id="designer",
        policy=POLICY,
        funnel_id="spec-review",
        artifact_ref="ref",
        artifact="the spec",
        run_id=run_id,
    )
    return queue


# ---- the gate names its run --------------------------------------------------------------------


def test_a_gate_records_the_run_that_produced_it(tmp_path: Path) -> None:
    queue = _open(tmp_path, "job-abc:designer", "job-abc")

    assert (queue.list_all()[0].output or {}).get("run_id") == "job-abc"


def test_both_gate_shapes_record_the_same_field(tmp_path: Path) -> None:
    """A stage's gate id and an in-node gate's differ; what a reader links on must not."""
    stage = _open(tmp_path / "a", "WMS-27:design", "WMS-27:design")
    in_node = _open(tmp_path / "b", "job-abc:designer", "job-abc")

    assert (stage.list_all()[0].output or {}).get("run_id") == "WMS-27:design"
    assert (in_node.list_all()[0].output or {}).get("run_id") == "job-abc"


def test_the_run_id_reaches_the_review_api(tmp_path: Path) -> None:
    """The gates page reads the review list directly, so it must not need a second fetch."""
    from swarmkit_runtime.server._routes_review import _item_to_dict  # noqa: PLC0415

    queue = _open(tmp_path, "job-abc:designer", "job-abc")

    assert _item_to_dict(queue.list_all()[0])["run_id"] == "job-abc"


def test_the_run_id_reaches_the_gate_state(tmp_path: Path) -> None:
    from swarmkit_runtime.gate_state import gate_state_for_policy  # noqa: PLC0415
    from swarmkit_runtime.governance._approval import RoleRegistry  # noqa: PLC0415

    queue = _open(tmp_path, "job-abc:designer", "job-abc")

    state = gate_state_for_policy(queue, RoleRegistry(roles={}), POLICY, "job-abc:designer")

    assert state.run_id == "job-abc"
    assert state.to_dict()["run_id"] == "job-abc"


def test_a_gate_without_a_run_id_still_reads(tmp_path: Path) -> None:
    """Items written before the field existed must not break a reader — the UI falls back to the
    split for exactly these."""
    from swarmkit_runtime.server._routes_review import _item_to_dict  # noqa: PLC0415

    queue = FileReviewQueue(tmp_path)
    open_gate(
        queue,
        gate_id="old:designer",
        topology_id="t",
        agent_id="designer",
        policy=POLICY,
        funnel_id="f",
    )

    assert _item_to_dict(queue.list_all()[0])["run_id"] == ""


def test_the_ui_links_to_the_job_not_a_saga() -> None:
    """The reported defect, asserted against the page: `/runs` is the saga surface and an in-node
    gate has no saga."""
    root = Path(__file__).resolve().parents[3] / "packages/ui/app/gates/page.tsx"
    src = root.read_text()

    assert "/job/?id=" in src
    assert "/runs?run=" not in src


# ---- artifacts over HTTP ------------------------------------------------------------------------


def _store(tmp_path: Path) -> Any:
    return build_artifact_store(
        {"backend": "filesystem"},
        workspace_root=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'a.sqlite'}",
    )


def test_an_artifact_is_fetchable_by_ref(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ref = store.put("WMS-35", "run-1", "the resolution")

    assert store.get(ref) == "the resolution"


def test_refs_are_listable_by_correlation(tmp_path: Path) -> None:
    """The read half of the correlation chain: given a ticket, what did its runs produce."""
    store = _store(tmp_path)
    store.put("WMS-35", "run-1", "a")
    store.put("WMS-35", "run-2", "b")
    store.put("WMS-36", "run-3", "c")

    assert sorted(store.list("WMS-35")) == ["WMS-35/run-1/output", "WMS-35/run-2/output"]


@pytest.mark.parametrize(
    "needle",
    [
        '@app.get("/artifacts/{ref:path}")',  # a ref contains slashes
        '@app.get("/artifacts")',
        "no artifact store is configured",  # 503, not a 500
    ],
)
def test_the_artifact_routes_are_registered(needle: str) -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_routes_introspection.py"
    ).read_text()

    assert needle in src


def test_a_missing_artifact_is_a_404_not_an_empty_string() -> None:
    """So a caller threading one run's output onward cannot carry "" forward as the output."""
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_routes_introspection.py"
    ).read_text()
    handler = src[src.index('@app.get("/artifacts/{ref:path}")') :][:1400]

    assert "status_code=404" in handler
