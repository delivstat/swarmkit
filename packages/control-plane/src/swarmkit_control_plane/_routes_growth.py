"""Aggregation rollups + growth-loop proposal/gap routes (docs 14, 17)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request

from swarmkit_control_plane._aggregation import KINDS, AggregationStore
from swarmkit_control_plane._artifacts import KINDS as ARTIFACT_KINDS
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._connector import ConnectorError
from swarmkit_control_plane._fntypes import (
    AuthorFn,
    EvalFn,
)
from swarmkit_control_plane._fntypes import extract_artifact as _extract_artifact
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._schemas import (
    AggregateRequest,
    DecisionRequest,
    GapProposeRequest,
    ProposalRequest,
)


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


def _mount_proposals(app: FastAPI, store: ProposalStore, artifacts: ArtifactStore) -> None:
    """Growth-loop approval queue (doc 17). Approving a proposal publishes it to the registry —
    the human gate. Nothing auto-approves; approve/reject are operator-only (machines can't)."""

    @app.post("/proposals")
    async def create_proposal(req: ProposalRequest) -> dict[str, Any]:
        if req.kind not in ARTIFACT_KINDS:
            raise HTTPException(404, f"unknown kind '{req.kind}'")
        return store.create(
            kind=req.kind,
            artifact_id=req.artifact_id,
            content=req.content,
            proposed_by=req.proposed_by,
            signal=req.signal,
            eval_summary=req.eval_summary,
        )

    @app.get("/proposals")
    async def list_proposals(status: str | None = None) -> list[dict[str, Any]]:
        return store.list(status)

    @app.get("/proposals/{proposal_id}")
    async def get_proposal(proposal_id: str) -> dict[str, Any]:
        found = store.get(proposal_id)
        if found is None:
            raise HTTPException(404, "proposal not found")
        return found

    @app.post("/proposals/{proposal_id}/approve")
    async def approve_proposal(
        proposal_id: str, req: DecisionRequest, request: Request
    ) -> dict[str, Any]:
        prop = store.get(proposal_id)
        if prop is None:
            raise HTTPException(404, "proposal not found")
        # The approver: an OIDC human's subject when available, else the named operator.
        principal = getattr(request.state, "principal", None)
        approver = (getattr(principal, "subject", None) or "") if principal else ""
        approver = approver or req.approved_by
        # Approval IS publication: the proposed content becomes a new registry version (provenance
        # carries both the proposer and the approving human).
        published = artifacts.register_version(
            prop["kind"],
            prop["artifact_id"],
            content=prop["content"],
            authored_by=f"{prop['proposed_by']} (approved by {approver})".strip(),
        )
        try:
            return store.mark_approved(
                proposal_id, approved_by=approver, published_version=published["version"]
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/proposals/{proposal_id}/reject")
    async def reject_proposal(
        proposal_id: str, req: DecisionRequest, request: Request
    ) -> dict[str, Any]:
        principal = getattr(request.state, "principal", None)
        approver = (
            (getattr(principal, "subject", None) or "") if principal else ""
        ) or req.approved_by
        try:
            return store.mark_rejected(proposal_id, approved_by=approver, reason=req.reason)
        except KeyError as exc:
            raise HTTPException(404, "proposal not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


def _mount_growth(
    app: FastAPI,
    registry: SqliteRegistry,
    proposals: ProposalStore,
    author: AuthorFn,
    eval_run: EvalFn,
) -> None:
    """Growth-loop automation (doc 17): signal → surface → propose → test. A ranked gap
    (GET /gaps) is turned into a *drafted, eval-tested proposal* in one operator action —
    the authoring swarm drafts a fix, an eval topology tests it, and the result lands in
    the approval queue as `pending`. The human gate is untouched: this only ever creates a
    pending proposal (approve == publish, humans only). Operator-only (authorize denies
    connector tokens); Mode A only — drafting drives a swarm on a reachable instance."""

    @app.post("/gaps/propose")
    async def propose_from_gap(req: GapProposeRequest) -> dict[str, Any]:
        inst = registry.get(req.instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if inst.connection != "direct":
            raise HTTPException(409, "auto-draft requires a directly-reachable (Mode A) instance")
        # Propose (draft): the authoring swarm drafts a fix for the gap.
        prompt = (
            f"A worker needs the capability '{req.capability}' but no skill provides it. "
            f"{req.description} Draft a skill that closes this gap."
        )
        try:
            drafted = await author(inst.endpoint, inst.token_ref, req.topology, prompt)
        except ConnectorError as exc:
            raise HTTPException(502, f"authoring run failed: {exc}") from exc
        artifact = _extract_artifact(drafted.get("reply") or "")
        if artifact is None:
            raise HTTPException(422, "the authoring swarm did not produce a draftable artifact")
        # Test: run an eval topology on the draft (never blocks — returns a status summary).
        eval_summary = await eval_run(
            inst.endpoint, inst.token_ref, req.eval_topology, json.dumps(artifact["content"])
        )
        # Land it in the approval queue as pending (human gate intact).
        return proposals.create(
            kind=artifact["kind"],
            artifact_id=artifact["id"],
            content=artifact["content"],
            proposed_by="authoring-swarm",
            signal=f"gap:{req.capability}",
            eval_summary=eval_summary,
        )
