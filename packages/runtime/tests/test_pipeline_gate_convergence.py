"""A bundled pipeline's parked stage carries a real approval policy (slice 4).

design/details/pipeline-gate-convergence.md. Before this, the two parking mechanisms were:

- **path A** (``build_pipeline_run_stage``, what ``swarmkit serve`` runs) — parked durably but
  never opened a gate: no policy, no role-tasks, no quorum, released only by an operator emitting
  the ``gate`` event under the reserved ``pipeline:advance`` scope;
- **path B** (the agent funnel's ``approve`` layer) — real multi-party semantics, but parked by
  blocking a coroutine that a restart destroys.

Durable without a policy, or a policy that cannot survive a restart. This closes A's half: the
stage opens its funnel's policy before parking, and the last resolution emits the ``gate`` event
that resumes the run.

The restart test is the acceptance criterion from the note — path B cannot pass it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, ClassVar

import pytest
from conftest import copy_workspace
from swarmkit_runtime.artifacts import build_artifact_store
from swarmkit_runtime.governance._approval import ApprovalPolicy, GateStatus, evaluate
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import collect_resolutions

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"

ROLES = """\
apiVersion: swarmkit/v1
kind: RoleRegistry
metadata:
  id: demo-roles
  name: Demo roles
roles:
  - id: security-reviewer
    members: [alice]
    scopes: [greet:approve]
  - id: release-manager
    members: [bob]
    scopes: [greet:approve]
"""

FUNNEL = """\
apiVersion: swarmkit/v1
kind: Funnel
metadata:
  id: greet-gate
  name: Greet gate
  description: Both leads sign off.
approve:
  rules:
    - scope: greet:approve
      roles: [security-reviewer, release-manager]
      quorum: all
provenance:
  authored_by: human
  version: 1.0.0
"""

CID = "run-42"
STAGE = "greeter"
GATE = f"{CID}:{STAGE}"


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    copy_workspace(EXAMPLE_WS, ws)
    shutil.rmtree(ws / ".swarmkit", ignore_errors=True)
    (ws / ".swarmkit").mkdir()
    (ws / "roles").mkdir(exist_ok=True)
    (ws / "roles" / "r.yaml").write_text(ROLES)
    (ws / "funnels").mkdir(exist_ok=True)
    (ws / "funnels" / "f.yaml").write_text(FUNNEL)
    return ws


class _Result:
    output = "the produced artifact"


class _Runtime:
    """Minimal WorkspaceRuntime stand-in: a resolved workspace + a scripted run."""

    def __init__(self, ws: Path) -> None:
        self.workspace = resolve_workspace(ws)
        self.workspace_root = ws
        self.governance: Any = None

    async def run(self, *a: Any, **k: Any) -> Any:
        return _Result()


class _SagaStore:
    class _Saga:
        input = "a real payload"
        passed_stages: ClassVar[list[str]] = []

    def get(self, _cid: str) -> Any:
        return self._Saga()


def _run_stage_for(ws: Path) -> Any:
    from swarmkit_runtime.server._pipeline_stage import build_pipeline_run_stage  # noqa: PLC0415

    return build_pipeline_run_stage(
        _Runtime(ws),  # type: ignore[arg-type]
        build_artifact_store(
            None, workspace_root=ws, database_url=f"sqlite:///{ws / '.swarmkit' / 's.sqlite'}"
        ),
        _SagaStore(),  # type: ignore[arg-type]
    )


# ---- path A now opens a policy ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_gated_stage_opens_its_role_tasks(tmp_path: Path) -> None:
    """The core of the slice: before this, a bundled parked run had NO role-tasks at all."""
    ws = _workspace(tmp_path)
    outcome = await _run_stage_for(ws)(
        CID, {"id": STAGE, "topology": "hello", "gate": "greet-gate"}
    )

    assert outcome.status == "parked"
    items = [i for i in FileReviewQueue(ws).list_all() if i.output.get("gate_id") == GATE]
    assert {i.output["role"] for i in items} == {"security-reviewer", "release-manager"}
    assert all(i.status == "pending" for i in items)
    # The funnel is recorded on each item so a resolver can rebuild the policy without walking
    # saga -> graph -> stage.
    assert {i.output["funnel_id"] for i in items} == {"greet-gate"}


@pytest.mark.asyncio
async def test_an_ungated_stage_opens_nothing(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    outcome = await _run_stage_for(ws)(CID, {"id": STAGE, "topology": "hello"})
    assert outcome.status == "completed"
    assert FileReviewQueue(ws).list_all() == []


@pytest.mark.asyncio
async def test_an_unresolvable_gate_still_parks_without_role_tasks(tmp_path: Path) -> None:
    """A stage whose `gate` names a funnel this workspace does not carry — an externally-driven
    gate, or a since-renamed funnel. It must still park (releasable via `pipeline advance`) rather
    than fail the stage or raise.

    Note the funnel SCHEMA requires an `approve` block, so "a funnel that is a checkpoint rather
    than a vote" is not expressible; the unresolvable case is the reachable one.
    """
    ws = _workspace(tmp_path)
    outcome = await _run_stage_for(ws)(
        CID, {"id": STAGE, "topology": "hello", "gate": "no-such-funnel"}
    )
    assert outcome.status == "parked"
    assert FileReviewQueue(ws).list_all() == []


@pytest.mark.asyncio
async def test_reopening_a_retried_stage_keeps_approvals_already_cast(tmp_path: Path) -> None:
    """`open_gate` is idempotent by design. Documented in the note as an open question: those
    approvals were cast against the PREVIOUS artifact — but silently discarding a human decision is
    worse than carrying it, so today's behaviour is pinned rather than changed."""
    ws = _workspace(tmp_path)
    run_stage = _run_stage_for(ws)
    stage = {"id": STAGE, "topology": "hello", "gate": "greet-gate"}
    await run_stage(CID, stage)

    queue = FileReviewQueue(ws)
    first = next(i for i in queue.list_all() if i.output["role"] == "security-reviewer")
    queue.record_resolution(first.id, "approved", "alice")

    await run_stage(CID, stage)  # retry

    again = next(
        i for i in FileReviewQueue(ws).list_all() if i.output["role"] == "security-reviewer"
    )
    assert again.status == "approved" and again.answer == "alice"


# ---- the acceptance test: restart survival ------------------------------------------------------


@pytest.mark.asyncio
async def test_the_gate_survives_losing_all_process_state(tmp_path: Path) -> None:
    """Park a run, DROP every in-process object, then resolve and assert quorum is reached.

    Path B cannot pass this: it parks by holding a coroutine in `resolve_multiparty`'s poll loop,
    which a restart destroys. Path A never blocks, so the gate lives entirely on the queue.
    """
    ws = _workspace(tmp_path)
    outcome = await _run_stage_for(ws)(
        CID, {"id": STAGE, "topology": "hello", "gate": "greet-gate"}
    )
    assert outcome.status == "parked"

    del outcome  # everything the "old process" held

    # A fresh process: new queue handle, new workspace resolution, nothing carried over.
    queue = FileReviewQueue(ws)
    workspace = resolve_workspace(ws)
    policy = ApprovalPolicy.from_dict(workspace.funnels["greet-gate"].spec["approve"])

    for item in queue.list_all():
        assert item.status == "pending"

    sec = next(i for i in queue.list_all() if i.output["role"] == "security-reviewer")
    queue.record_resolution(sec.id, "approved", "alice")
    partial = evaluate(
        policy, workspace.role_registry, collect_resolutions(queue, gate_id=GATE, policy=policy)
    )
    assert partial.status is not GateStatus.APPROVED, "quorum is `all` — one is not enough"

    rel = next(i for i in queue.list_all() if i.output["role"] == "release-manager")
    queue.record_resolution(rel.id, "approved", "bob")
    final = evaluate(
        policy, workspace.role_registry, collect_resolutions(queue, gate_id=GATE, policy=policy)
    )
    assert final.status is GateStatus.APPROVED
    assert final.distinct_approvers == frozenset({"alice", "bob"})


# ---- resolution resumes the run -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_completing_the_gate_signals_the_run(tmp_path: Path) -> None:
    """The last resolution emits the `gate` event the controller waits on — so `pipeline advance`
    reverts to break-glass instead of being the only way out."""
    from swarmkit_runtime.governance._mock import MockGovernanceProvider  # noqa: PLC0415
    from swarmkit_runtime.server._routes_review import _resume_if_gate_resolved  # noqa: PLC0415

    ws = _workspace(tmp_path)
    await _run_stage_for(ws)(CID, {"id": STAGE, "topology": "hello", "gate": "greet-gate"})

    signalled: list[tuple[str, str]] = []

    async def _signal(cid: str, event: str) -> None:
        signalled.append((cid, event))

    class _Rt:
        workspace = resolve_workspace(ws)
        governance = MockGovernanceProvider(allowed_scopes=frozenset({"approvals:resolve"}))

    queue = FileReviewQueue(ws)
    sec = next(i for i in queue.list_all() if i.output["role"] == "security-reviewer")
    rel = next(i for i in queue.list_all() if i.output["role"] == "release-manager")

    queue.record_resolution(sec.id, "approved", "alice")
    await _resume_if_gate_resolved(_Rt(), _signal, queue, sec, "alice")
    assert signalled == [], "quorum not met — the run must stay parked"

    queue.record_resolution(rel.id, "approved", "bob")
    await _resume_if_gate_resolved(_Rt(), _signal, queue, rel, "bob")

    assert len(signalled) == 1
    cid, event = signalled[0]
    assert cid == CID
    assert json.loads(event) == {"kind": "gate", "approved": True, "stage": STAGE}


@pytest.mark.asyncio
async def test_a_rejection_also_resumes_the_run(tmp_path: Path) -> None:
    """A reject is terminal for the gate; the controller needs to hear it too, or the saga parks
    forever on a decision that was already made."""
    from swarmkit_runtime.governance._mock import MockGovernanceProvider  # noqa: PLC0415
    from swarmkit_runtime.server._routes_review import _resume_if_gate_resolved  # noqa: PLC0415

    ws = _workspace(tmp_path)
    await _run_stage_for(ws)(CID, {"id": STAGE, "topology": "hello", "gate": "greet-gate"})

    signalled: list[tuple[str, str]] = []

    async def _signal(cid: str, event: str) -> None:
        signalled.append((cid, event))

    class _Rt:
        workspace = resolve_workspace(ws)
        governance = MockGovernanceProvider(allowed_scopes=frozenset({"approvals:resolve"}))

    queue = FileReviewQueue(ws)
    sec = next(i for i in queue.list_all() if i.output["role"] == "security-reviewer")
    queue.record_resolution(sec.id, "rejected", "alice")
    await _resume_if_gate_resolved(_Rt(), _signal, queue, sec, "alice")

    assert len(signalled) == 1
    assert json.loads(signalled[0][1]) == {"kind": "gate", "approved": False, "stage": STAGE}


@pytest.mark.asyncio
async def test_no_signal_sink_still_records_the_resolution(tmp_path: Path) -> None:
    """A gate must never become unresolvable because the sink is absent."""
    from swarmkit_runtime.server._routes_review import _resume_if_gate_resolved  # noqa: PLC0415

    ws = _workspace(tmp_path)
    await _run_stage_for(ws)(CID, {"id": STAGE, "topology": "hello", "gate": "greet-gate"})
    queue = FileReviewQueue(ws)
    sec = next(i for i in queue.list_all() if i.output["role"] == "security-reviewer")
    queue.record_resolution(sec.id, "approved", "alice")

    class _Rt:
        workspace = resolve_workspace(ws)

    await _resume_if_gate_resolved(_Rt(), None, queue, sec, "alice")  # must not raise
    assert queue.get(sec.id).status == "approved"  # type: ignore[union-attr]
