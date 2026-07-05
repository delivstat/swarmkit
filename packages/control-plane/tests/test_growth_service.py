"""Unit tests for GrowthService — the growth-loop logic, exercised directly (no HTTP).

The service is the seam the routes delegate to; testing it here proves the business rules
(atomic approve, the human-gate transitions, the propose→draft→eval pipeline) without a
TestClient. The headline is ``test_approve_is_atomic_no_duplicate_publish``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._connector import ConnectorError
from swarmkit_control_plane._models import ConnectionMode, Instance
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._service import (
    ConflictError,
    GrowthService,
    NotFoundError,
    UnprocessableError,
    UpstreamError,
)

_DRAFT = '{"kind": "skill", "id": "pdf-extract", "content": {"category": "capability"}}'


def _service(
    tmp_path: Path, *, author: Any = None, eval_run: Any = None
) -> tuple[GrowthService, ProposalStore, ArtifactStore, SqliteRegistry]:
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    proposals = ProposalStore(db)
    artifacts = ArtifactStore(db)

    async def _default_author(
        endpoint: str, token_ref: str, topology: str, message: str
    ) -> dict[str, Any]:
        return {"reply": f"Draft: {_DRAFT}", "status": "completed"}

    async def _default_eval(
        endpoint: str, token_ref: str, eval_topology: str, payload: str
    ) -> dict[str, Any]:
        return {"passed": 9, "total": 10, "pass_rate": 0.9, "status": "completed"}

    svc = GrowthService(
        registry, proposals, artifacts, author or _default_author, eval_run or _default_eval
    )
    return svc, proposals, artifacts, registry


def _pending(svc: GrowthService) -> str:
    prop = svc.create_proposal(
        kind="skill",
        artifact_id="web-search",
        content={"category": "capability"},
        proposed_by="authoring-swarm",
        signal="gap",
    )
    return str(prop["id"])


# ---- approve ----------------------------------------------------------------


def test_approve_publishes_and_marks_approved(tmp_path: Path) -> None:
    svc, _, artifacts, _ = _service(tmp_path)
    pid = _pending(svc)
    result = svc.approve(pid, approver="alice@example.com")
    assert result["status"] == "approved"
    assert result["published_version"] == "v1"
    assert result["approved_by"] == "alice@example.com"
    arts = artifacts.list_artifacts()
    assert len(arts) == 1 and arts[0]["id"] == "web-search" and arts[0]["latest_version"] == "v1"
    # Provenance carries both the proposer and the approving human.
    ver = artifacts.get_version("skill", "web-search", "v1")
    assert ver is not None and "approved by alice@example.com" in ver["authored_by"]


def test_approve_is_atomic_no_duplicate_publish(tmp_path: Path) -> None:
    """The headline guarantee: a proposal can be approved at most once, publishing exactly one
    version. The second approval loses the claim (raises Conflict) and never reaches the
    registry — so no duplicate identical version is minted."""
    svc, _, artifacts, _ = _service(tmp_path)
    pid = _pending(svc)

    svc.approve(pid, approver="alice")
    with pytest.raises(ConflictError):
        svc.approve(pid, approver="bob")

    # Exactly one version exists — the claim guard prevented a second publish.
    versions = artifacts.list_versions("skill", "web-search")
    assert len(versions) == 1 and versions[0]["version"] == "v1"


def test_approve_after_a_claim_by_the_store_does_not_publish(tmp_path: Path) -> None:
    """Simulate the losing side of a concurrent race: the proposal is already claimed (approved)
    by another actor before this approve runs. approve must raise Conflict and publish nothing."""
    svc, proposals, artifacts, _ = _service(tmp_path)
    pid = _pending(svc)
    # Another worker won the claim first (no publish yet — mid-saga).
    proposals.mark_approved(pid, approved_by="winner", published_version="")

    with pytest.raises(ConflictError):
        svc.approve(pid, approver="loser")
    assert artifacts.list_artifacts() == []  # the loser published nothing


def test_approve_missing_is_not_found(tmp_path: Path) -> None:
    svc, _, _, _ = _service(tmp_path)
    with pytest.raises(NotFoundError):
        svc.approve("nope", approver="alice")


# ---- reject -----------------------------------------------------------------


def test_reject_marks_rejected_and_publishes_nothing(tmp_path: Path) -> None:
    svc, _, artifacts, _ = _service(tmp_path)
    pid = _pending(svc)
    result = svc.reject(pid, approver="alice", reason="regresses smoke evals")
    assert result["status"] == "rejected" and result["reason"] == "regresses smoke evals"
    assert artifacts.list_artifacts() == []


def test_cannot_re_decide(tmp_path: Path) -> None:
    svc, _, _, _ = _service(tmp_path)
    pid = _pending(svc)
    svc.approve(pid, approver="alice")
    with pytest.raises(ConflictError):
        svc.reject(pid, approver="bob", reason="")


def test_reject_missing_is_not_found(tmp_path: Path) -> None:
    svc, _, _, _ = _service(tmp_path)
    with pytest.raises(NotFoundError):
        svc.reject("nope", approver="alice", reason="")


# ---- create validation ------------------------------------------------------


def test_create_rejects_unknown_kind(tmp_path: Path) -> None:
    svc, _, _, _ = _service(tmp_path)
    with pytest.raises(NotFoundError):
        svc.create_proposal(kind="gadget", artifact_id="x", content={})


# ---- propose_from_gap -------------------------------------------------------


def _enroll(registry: SqliteRegistry, connection: ConnectionMode = "direct") -> str:
    inst = Instance(
        id="i1", name="edge", endpoint="http://serve:8000", connection=connection, tier="run"
    )
    registry.add(inst)
    return inst.id


@pytest.mark.asyncio
async def test_propose_from_gap_drafts_evaluates_and_queues(tmp_path: Path) -> None:
    svc, proposals, _, registry = _service(tmp_path)
    iid = _enroll(registry)
    prop = await svc.propose_from_gap(instance_id=iid, capability="pdf-extract")
    assert prop["status"] == "pending"  # human gate intact
    assert prop["kind"] == "skill" and prop["artifact_id"] == "pdf-extract"
    assert prop["proposed_by"] == "authoring-swarm"
    assert prop["signal"] == "gap:pdf-extract"
    assert prop["eval_summary"]["pass_rate"] == 0.9
    assert len(proposals.list("pending")) == 1


@pytest.mark.asyncio
async def test_propose_missing_instance_is_not_found(tmp_path: Path) -> None:
    svc, _, _, _ = _service(tmp_path)
    with pytest.raises(NotFoundError):
        await svc.propose_from_gap(instance_id="ghost", capability="x")


@pytest.mark.asyncio
async def test_propose_poll_instance_is_conflict(tmp_path: Path) -> None:
    svc, _, _, registry = _service(tmp_path)
    iid = _enroll(registry, "poll")
    with pytest.raises(ConflictError):
        await svc.propose_from_gap(instance_id=iid, capability="x")


@pytest.mark.asyncio
async def test_propose_no_draftable_artifact_is_unprocessable(tmp_path: Path) -> None:
    async def chatty(endpoint: str, token_ref: str, topology: str, message: str) -> dict[str, Any]:
        return {"reply": "Sure, tell me more.", "status": "completed"}

    svc, _, _, registry = _service(tmp_path, author=chatty)
    iid = _enroll(registry)
    with pytest.raises(UnprocessableError):
        await svc.propose_from_gap(instance_id=iid, capability="x")


@pytest.mark.asyncio
async def test_propose_authoring_unreachable_is_upstream(tmp_path: Path) -> None:
    async def boom(endpoint: str, token_ref: str, topology: str, message: str) -> dict[str, Any]:
        raise ConnectorError("run failed")

    svc, _, _, registry = _service(tmp_path, author=boom)
    iid = _enroll(registry)
    with pytest.raises(UpstreamError):
        await svc.propose_from_gap(instance_id=iid, capability="x")


@pytest.mark.asyncio
async def test_propose_survives_a_failing_eval(tmp_path: Path) -> None:
    async def eval_down(
        endpoint: str, token_ref: str, eval_topology: str, payload: str
    ) -> dict[str, Any]:
        return {"status": "no-eval-topology"}

    svc, _, _, registry = _service(tmp_path, eval_run=eval_down)
    iid = _enroll(registry)
    prop = await svc.propose_from_gap(instance_id=iid, capability="x")
    assert prop["status"] == "pending"
    assert prop["eval_summary"]["status"] == "no-eval-topology"
