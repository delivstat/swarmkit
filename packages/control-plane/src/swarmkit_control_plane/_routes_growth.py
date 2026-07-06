"""Aggregation rollups + growth-loop proposal/gap routes (docs 14, 17).

The proposal/gap routes are thin: they read HTTP concerns (the acting principal) and delegate the
business logic to :class:`GrowthService`, mapping its :class:`ServiceError` to a status code.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request

from swarmkit_control_plane._aggregation import KINDS, AggregationStore
from swarmkit_control_plane._schemas import (
    AggregateRequest,
    DecisionRequest,
    GapProposeRequest,
    ProposalRequest,
)
from swarmkit_control_plane._service import GrowthService, ServiceError


def _approver(request: Request, fallback: str) -> str:
    """The deciding identity: an OIDC human's subject when present, else the named operator."""
    principal = getattr(request.state, "principal", None)
    subject = (getattr(principal, "subject", None) or "") if principal else ""
    return subject or fallback


def _mount_aggregation(app: FastAPI, agg: AggregationStore) -> None:
    """Push-aggregation API + SwarmKit-specific rollups (doc 14). Instances push their own audit/
    eval/usage; operators read the fleet rollups."""

    @app.post("/aggregate/{kind}")
    async def aggregate(kind: str, req: AggregateRequest, request: Request) -> dict[str, int]:
        if kind not in KINDS:
            raise HTTPException(404, f"unknown signal '{kind}' — use {'/'.join(KINDS)}")
        # A connector pushes as itself (id from the authenticated principal — no spoofing); an
        # operator (or open-mode caller) must name the instance in the body.
        principal = getattr(request.state, "principal", None)
        if principal is not None and principal.kind == "connector":
            instance_id = principal.instance_id
        else:
            instance_id = req.instance_id
        if not instance_id:
            raise HTTPException(400, "instance_id required (omit only when pushing as a connector)")
        return agg.ingest(instance_id, kind, req.records)

    @app.get("/usage")
    async def usage() -> list[dict[str, Any]]:
        return agg.usage_rollup()

    @app.get("/eval")
    async def eval_summary() -> list[dict[str, Any]]:
        return agg.eval_summary()

    @app.get("/audit")
    async def audit(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
        # Bounded: sqlite treats a negative LIMIT as unbounded, so an unvalidated ?limit=-1
        # would dump the entire append-only audit log; a huge positive value is a memory spike.
        return agg.recent_audit(limit)

    @app.get("/gaps")
    async def gaps() -> list[dict[str, Any]]:
        """Skill gaps ranked across the fleet (signal → surface, doc 17)."""
        return agg.gap_rollup()


def _mount_proposals(app: FastAPI, service: GrowthService) -> None:
    """Growth-loop approval queue (doc 17). Approving a proposal publishes it to the registry —
    the human gate. Nothing auto-approves; approve/reject are operator-only (machines can't)."""

    @app.post("/proposals")
    async def create_proposal(req: ProposalRequest) -> dict[str, Any]:
        try:
            return service.create_proposal(
                kind=req.kind,
                artifact_id=req.artifact_id,
                content=req.content,
                proposed_by=req.proposed_by,
                signal=req.signal,
                eval_summary=req.eval_summary,
            )
        except ServiceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.get("/proposals")
    async def list_proposals(status: str | None = None) -> list[dict[str, Any]]:
        return service.list_proposals(status)

    @app.get("/proposals/{proposal_id}")
    async def get_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            return service.get_proposal(proposal_id)
        except ServiceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.post("/proposals/{proposal_id}/approve")
    async def approve_proposal(
        proposal_id: str, req: DecisionRequest, request: Request
    ) -> dict[str, Any]:
        try:
            return service.approve(proposal_id, approver=_approver(request, req.approved_by))
        except ServiceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.post("/proposals/{proposal_id}/reject")
    async def reject_proposal(
        proposal_id: str, req: DecisionRequest, request: Request
    ) -> dict[str, Any]:
        try:
            return service.reject(
                proposal_id, approver=_approver(request, req.approved_by), reason=req.reason
            )
        except ServiceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc


def _mount_growth(app: FastAPI, service: GrowthService) -> None:
    """Growth-loop automation (doc 17): signal → surface → propose → test. A ranked gap
    (GET /gaps) is turned into a *drafted, eval-tested proposal* in one operator action —
    the authoring swarm drafts a fix, an eval topology tests it, and the result lands in the
    approval queue as `pending`. The human gate is untouched: this only ever creates a
    pending proposal (approve == publish, humans only). Operator-only (authorize denies
    connector tokens); Mode A only — drafting drives a swarm on a reachable instance."""

    @app.post("/gaps/propose")
    async def propose_from_gap(req: GapProposeRequest) -> dict[str, Any]:
        try:
            return await service.propose_from_gap(
                instance_id=req.instance_id,
                capability=req.capability,
                description=req.description,
                topology=req.topology,
                eval_topology=req.eval_topology,
            )
        except ServiceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
