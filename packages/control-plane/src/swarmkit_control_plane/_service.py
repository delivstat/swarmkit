"""GrowthService — the growth-loop business logic (propose → approve/reject), lifted out of the
route closures so it is unit-testable without HTTP *and* so approval is atomic.

The routes in ``_routes_growth`` are now thin: they read HTTP concerns (the acting principal),
call one of these methods, and map the domain errors below to status codes. The service owns no
HTTP: it takes plain arguments and raises :class:`GrowthError` subclasses whose ``status`` the
route translates. See design/details/control-plane/17-growth-loop.md.

Atomicity (``approve``): the panel's stores share one sqlite file, but ``register_version``
(publish) and ``mark_approved`` (record the decision) were two separate writes issued
publish-first — so two concurrent approvals of the same proposal could both publish before
either recorded the decision, minting duplicate identical registry versions
(``register_version``'s idempotency only protects *sequential* callers). ``approve`` now
**claims first**: it transitions the proposal pending→approved *before* publishing, so only the
claim winner ever reaches the registry; the published version is backfilled afterwards.
"""

from __future__ import annotations

import json
from typing import Any

from swarmkit_control_plane._artifacts import KINDS as ARTIFACT_KINDS
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._connector import ConnectorError
from swarmkit_control_plane._fntypes import AuthorFn, EvalFn, extract_artifact
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry


class GrowthError(Exception):
    """Base for growth-loop domain errors. ``status`` is the HTTP code the route maps it to."""

    status = 400


class NotFoundError(GrowthError):
    status = 404


class ConflictError(GrowthError):
    status = 409


class UnprocessableError(GrowthError):
    status = 422


class UpstreamError(GrowthError):
    status = 502


class GrowthService:
    """Growth-loop operations over the proposal queue, artifact registry, and instance registry.

    Approve/reject are operator-only (the human gate — enforced by the route's authorize layer,
    not here); ``propose_from_gap`` drives a reachable instance's authoring swarm. Every method is
    a plain call that raises a :class:`GrowthError` on a domain failure.
    """

    def __init__(
        self,
        registry: SqliteRegistry,
        proposals: ProposalStore,
        artifacts: ArtifactStore,
        author: AuthorFn,
        eval_run: EvalFn,
    ) -> None:
        self._registry = registry
        self._proposals = proposals
        self._artifacts = artifacts
        self._author = author
        self._eval_run = eval_run

    # ---- approval queue -----------------------------------------------------

    def create_proposal(
        self,
        *,
        kind: str,
        artifact_id: str,
        content: Any,
        proposed_by: str = "",
        signal: str = "",
        eval_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if kind not in ARTIFACT_KINDS:
            raise NotFoundError(f"unknown kind '{kind}'")
        return self._proposals.create(
            kind=kind,
            artifact_id=artifact_id,
            content=content,
            proposed_by=proposed_by,
            signal=signal,
            eval_summary=eval_summary,
        )

    def list_proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        return self._proposals.list(status)

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        found = self._proposals.get(proposal_id)
        if found is None:
            raise NotFoundError("proposal not found")
        return found

    def approve(self, proposal_id: str, *, approver: str) -> dict[str, Any]:
        """Approve a proposal: publish its content as a new registry version, atomically.

        Claim-first (see the module docstring): mark the proposal approved — the atomic
        pending→approved guard — *before* touching the registry, so a losing concurrent
        approval fails the claim and never publishes. The version is backfilled after the push.
        """
        prop = self._proposals.get(proposal_id)
        if prop is None:
            raise NotFoundError("proposal not found")
        try:
            self._proposals.mark_approved(proposal_id, approved_by=approver, published_version="")
        except KeyError as exc:  # deleted between the read and the claim
            raise NotFoundError("proposal not found") from exc
        except ValueError as exc:  # not pending — already decided / lost the claim race
            raise ConflictError(str(exc)) from exc
        # Only the claim winner reaches here → exactly one publish, no duplicate versions.
        published = self._artifacts.register_version(
            prop["kind"],
            prop["artifact_id"],
            content=prop["content"],
            authored_by=f"{prop['proposed_by']} (approved by {approver})".strip(),
        )
        return self._proposals.set_published_version(proposal_id, published["version"])

    def reject(self, proposal_id: str, *, approver: str, reason: str) -> dict[str, Any]:
        try:
            return self._proposals.mark_rejected(proposal_id, approved_by=approver, reason=reason)
        except KeyError as exc:
            raise NotFoundError("proposal not found") from exc
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    # ---- growth automation --------------------------------------------------

    async def propose_from_gap(
        self,
        *,
        instance_id: str,
        capability: str,
        description: str = "",
        topology: str = "authoring",
        eval_topology: str = "eval",
    ) -> dict[str, Any]:
        """Turn a ranked gap into a drafted, eval-tested *pending* proposal (Mode A only).

        The authoring swarm drafts a fix, an eval topology tests it, and the result lands in the
        approval queue as ``pending`` — the human gate is never bypassed.
        """
        inst = self._registry.get(instance_id)
        if inst is None:
            raise NotFoundError("instance not found")
        if inst.connection != "direct":
            raise ConflictError("auto-draft requires a directly-reachable (Mode A) instance")
        prompt = (
            f"A worker needs the capability '{capability}' but no skill provides it. "
            f"{description} Draft a skill that closes this gap."
        )
        try:
            drafted = await self._author(inst.endpoint, inst.token_ref, topology, prompt)
        except ConnectorError as exc:
            raise UpstreamError(f"authoring run failed: {exc}") from exc
        artifact = extract_artifact(drafted.get("reply") or "")
        if artifact is None:
            raise UnprocessableError("the authoring swarm did not produce a draftable artifact")
        # The eval never blocks the proposal — a missing/failed eval still lands it with a status.
        eval_summary = await self._eval_run(
            inst.endpoint, inst.token_ref, eval_topology, json.dumps(artifact["content"])
        )
        return self._proposals.create(
            kind=artifact["kind"],
            artifact_id=artifact["id"],
            content=artifact["content"],
            proposed_by="authoring-swarm",
            signal=f"gap:{capability}",
            eval_summary=eval_summary,
        )
