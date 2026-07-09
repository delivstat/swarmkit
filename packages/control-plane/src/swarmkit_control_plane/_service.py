"""Panel service layer — the business logic behind the growth-loop and governed-deploy routes,
lifted out of the route closures so it is unit-testable without HTTP *and* so the multi-step
operations are atomic.

The routes in ``_routes_growth`` / ``_routes_artifacts`` are now thin: they read HTTP concerns
(the acting principal), call a service method, and map the domain errors below to status codes.
A service owns no HTTP — it takes plain arguments and raises :class:`ServiceError` subclasses whose
``status`` the route translates. See design/details/control-plane/17-growth-loop.md and
15-artifact-registry.md.

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
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from swarmkit_control_plane._aggregation import AggregationStore
from swarmkit_control_plane._artifacts import KINDS as ARTIFACT_KINDS
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._compat import incompatibility
from swarmkit_control_plane._connector import ConnectorError
from swarmkit_control_plane._credential_store import CredentialStore
from swarmkit_control_plane._deploy import DEPLOYABLE, DeployError
from swarmkit_control_plane._deploy_seq import DeploySeqStore
from swarmkit_control_plane._fleet_identity import FleetIdentity
from swarmkit_control_plane._fntypes import AuthorFn, DeployFn, EvalFn, extract_artifact
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry


class ServiceError(Exception):
    """Base for panel service domain errors. ``status`` is the HTTP code the route maps it to."""

    status = 400


class NotFoundError(ServiceError):
    status = 404


class BadRequestError(ServiceError):
    status = 400


class ForbiddenError(ServiceError):
    status = 403


class ConflictError(ServiceError):
    status = 409


class UnprocessableError(ServiceError):
    status = 422


class UpstreamError(ServiceError):
    status = 502


class GrowthService:
    """Growth-loop operations over the proposal queue, artifact registry, and instance registry.

    Approve/reject are operator-only (the human gate — enforced by the route's authorize layer,
    not here); ``propose_from_gap`` drives a reachable instance's authoring swarm. Every method is
    a plain call that raises a :class:`ServiceError` on a domain failure.
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


class DeployService:
    """Governed deploy of a published registry version onto an instance (doc 15 / doc 17 step 7).

    Deploy is legislative (the version was already human-approved to publish), so this is
    operator-gated at the route and always audited. Mode A pushes to the instance's serve /api;
    Mode B enqueues a ``deploy`` command for the connector to apply locally.
    """

    def __init__(
        self,
        registry: SqliteRegistry,
        artifacts: ArtifactStore,
        agg: AggregationStore,
        deploy: DeployFn,
        cred_store: CredentialStore | None = None,
        identity: FleetIdentity | None = None,
        deploy_seq: DeploySeqStore | None = None,
    ) -> None:
        self._registry = registry
        self._artifacts = artifacts
        self._agg = agg
        self._deploy = deploy
        self._cred_store = cred_store
        self._identity = identity
        self._deploy_seq = deploy_seq

    def _deploy_credential(self, instance_id: str, token_ref: str) -> str:
        """The credential the Mode-A deploy PUT carries (design 20). **Membership-first**: when the
        panel holds a membership for this instance it must be ``manage`` (else refuse — a monitor
        membership can't deploy, and we do *not* silently fall back to the legacy admin token); only
        when there's no membership do we fall back to the (deprecated) ``token_ref``."""
        if self._cred_store is not None:
            meta = self._cred_store.get_metadata(instance_id)
            if meta is not None:
                if meta.get("scope") != "manage":
                    raise ForbiddenError(
                        f"membership scope '{meta.get('scope')}' cannot deploy — enroll with "
                        "manage scope (or rotate to one)"
                    )
                secret = self._cred_store.get_secret(instance_id)
                if secret:
                    return secret
        if token_ref:
            return token_ref  # deprecated fallback for pre-enrollment instances
        raise ConflictError(
            "no manage membership credential and no token_ref — register the instance with "
            "manage scope first"
        )

    async def deploy(
        self, *, instance_id: str, kind: str, artifact_id: str, version: str, by: str = ""
    ) -> dict[str, Any]:
        """Push (Mode A) or enqueue (Mode B) a published version, then record intent + audit.

        Ordering matters: the registry-intended deployment is recorded only *after* the push /
        enqueue succeeds, so a failed Mode-A push leaves no phantom "deployed vX" record that
        drift would then misreport. *by* is the acting operator (an HTTP concern the route reads).
        """
        inst = self._registry.get(instance_id)
        if inst is None:
            raise NotFoundError("instance not found")
        if kind not in DEPLOYABLE:
            raise BadRequestError(f"kind '{kind}' is not deployable — use {'/'.join(DEPLOYABLE)}")
        ver = self._artifacts.get_version(kind, artifact_id, version)
        if ver is None:
            raise NotFoundError(f"no such version {kind}/{artifact_id}@{version}")

        # Schema-compatibility gate: refuse deploying what the instance can't validate (doc 15).
        reason = incompatibility(str(ver.get("schema_version", "")), inst.schema_version)
        if reason is not None:
            raise ConflictError(f"schema-incompatible deploy: {reason}")

        content = ver["content"]
        # Sign the deploy with the panel's fleet identity (design 22): the instance verifies the
        # signature against the pinned key before applying, so a stolen membership key alone can't
        # push. Signed over the registry content_hash (which serve recomputes from the content).
        signature = fleet_id = None
        deploy_seq: int | None = None
        if self._identity is not None:
            # A monotonic sequence, bound into the signature, is the downgrade guard (design 22):
            # the instance rejects any deploy whose sequence isn't newer than the last it applied.
            deploy_seq = self._deploy_seq.next() if self._deploy_seq is not None else None
            signature = self._identity.sign_deploy(
                kind, artifact_id, str(ver["content_hash"]), deploy_seq
            )
            fleet_id = self._identity.fleet_id
        if inst.connection == "direct":
            # Governed deploy over the membership credential (design 20): PUT with the manage-scope
            # membership key the instance issued, not a separately-provisioned admin token.
            credential = self._deploy_credential(instance_id, inst.token_ref)
            try:
                result = await self._deploy(
                    inst.endpoint,
                    credential,
                    kind,
                    artifact_id,
                    content,
                    signature=signature,
                    fleet_id=fleet_id,
                    deploy_seq=deploy_seq,
                )
            except DeployError as exc:
                raise UpstreamError(f"deploy failed: {exc}") from exc
            outcome: dict[str, Any] = {"mode": "direct", "result": result}
        else:
            # Mode B: the connector applies locally over loopback; carry the signature + fleet_id +
            # sequence in the command so local serve can verify against the pinned key (design 22).
            args: dict[str, Any] = {"kind": kind, "id": artifact_id, "body": content}
            if signature:
                args["signature"] = signature
                args["fleet_id"] = fleet_id
                if deploy_seq is not None:
                    args["deploy_seq"] = deploy_seq
            cmd = self._registry.enqueue(instance_id, "deploy", args)
            outcome = {"mode": "poll", "command_id": cmd.cmd_id}

        # Record intent only AFTER the push/enqueue succeeds (see the docstring).
        self._artifacts.set_deployment(instance_id, kind, artifact_id, version)
        self._agg.ingest(
            instance_id,
            "audit",
            [
                {
                    "id": uuid4().hex,
                    "ts": datetime.now(UTC).isoformat(),
                    "action": "artifact.deploy",
                    "kind": kind,
                    "artifact_id": artifact_id,
                    "version": version,
                    "by": by,
                }
            ],
        )
        return {"status": "ok", "version": version, **outcome}
