"""Control-plane panel API — instance registry + enrollment + heartbeat receiver.

Slice 1 of the connector: Mode A (direct) enrollment verifies an instance by pulling its
/capabilities; Mode B (poll) instances register unverified and report via heartbeat. The
poll command-queue and the fleet UI are later slices. See
design/details/control-plane/13-connector-registry.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from swarmkit_control_plane._aggregation import KINDS, AggregationStore
from swarmkit_control_plane._artifacts import KINDS as ARTIFACT_KINDS
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._auth import authenticate, authorize
from swarmkit_control_plane._connector import ConnectorError, fetch_capabilities
from swarmkit_control_plane._deploy import DEPLOYABLE, DeployError, push_artifact
from swarmkit_control_plane._models import Instance
from swarmkit_control_plane._oidc import OidcVerifier
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._tokens import mint_token
from swarmkit_control_plane._verbs import is_known_verb, tier_rank, verb_within_tier

VerifyFn = Callable[[str, str], Awaitable[dict[str, Any]]]
# (endpoint, token_ref, kind, artifact_id, content) -> serve response
DeployFn = Callable[[str, str, str, str, Any], Awaitable[dict[str, Any]]]


class EnrollRequest(BaseModel):
    name: str
    endpoint: str
    token_ref: str = ""
    connection: str = "direct"  # direct | poll
    tier: str = "read"  # granted transport tier — bounds enqueuable commands (Mode B)


class HeartbeatRequest(BaseModel):
    status: str = "ok"
    schema_version: str | None = None
    capabilities: dict[str, Any] | None = None


class EnqueueCommandRequest(BaseModel):
    verb: str
    args: dict[str, Any] = {}


class PollRequest(BaseModel):
    status: str = "ok"
    schema_version: str | None = None
    capabilities: dict[str, Any] | None = None


class CommandResultRequest(BaseModel):
    status: str = "done"  # done | error
    output: dict[str, Any] | None = None
    error: str | None = None


class MintTokenRequest(BaseModel):
    tier: str | None = None  # defaults to the instance's granted tier
    client_name: str = ""


class AggregateRequest(BaseModel):
    records: list[dict[str, Any]]
    # Only used by operators / open mode; connectors are scoped to their own id via the principal.
    instance_id: str | None = None


class RegisterVersionRequest(BaseModel):
    content: Any
    authored_by: str = ""
    schema_version: str = ""
    version: str | None = None


class DeploymentRequest(BaseModel):
    version: str


class ReportArtifactsRequest(BaseModel):
    records: list[dict[str, Any]]


class ProposalRequest(BaseModel):
    kind: str
    artifact_id: str
    content: Any
    proposed_by: str = ""
    signal: str = ""  # gap | eval_regression | drift | …
    eval_summary: dict[str, Any] = {}


class DecisionRequest(BaseModel):
    approved_by: str = ""
    reason: str = ""


class DeployRequest(BaseModel):
    kind: str
    artifact_id: str
    version: str


def create_app(
    registry: SqliteRegistry,
    *,
    verify: VerifyFn = fetch_capabilities,
    cors_origins: list[str] | None = None,
    operator_tokens: list[str] | None = None,
    oidc: OidcVerifier | None = None,
    aggregation: AggregationStore | None = None,
    observability: dict[str, str] | None = None,
    artifacts: ArtifactStore | None = None,
    proposals: ProposalStore | None = None,
    deploy: DeployFn = push_artifact,
) -> FastAPI:
    """Build the control-plane API. *verify* is injectable for testing.

    *cors_origins* are the exact browser origins allowed to call the panel (the fleet UI in a
    split-origin deploy). CORS is entirely config-driven — no origin is allowed unless listed here
    (CLI: --cors-origin / $SWARMKIT_CONTROL_PLANE_CORS_ORIGINS). For local dev, pass the UI's
    origin explicitly, e.g. --cors-origin http://localhost:3000.

    Auth is enabled when *operator_tokens* and/or *oidc* are configured; then every route except
    /health requires a bearer — an operator token (full access), a Mode B instance's per-instance
    token (scoped to its poll + result routes), or an OIDC JWT (human→panel, authenticates as an
    operator). When neither is set, the panel runs open (no auth) for local dev.
    """
    app = FastAPI(title="SwarmKit control plane")
    agg = aggregation or AggregationStore(registry.db_path)
    arts = artifacts or ArtifactStore(registry.db_path)
    props = proposals or ProposalStore(registry.db_path)
    ops = [t for t in (operator_tokens or []) if t]

    # Auth is registered before CORS so CORS stays the outermost layer (preflight + 401/403
    # responses still carry the configured CORS headers for the browser to read).
    if ops or oidc is not None:

        @app.middleware("http")
        async def auth_middleware(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            if request.url.path == "/health" or request.method == "OPTIONS":
                return await call_next(request)
            header = request.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else ""
            principal = authenticate(token, ops, registry, oidc)
            if principal is None:
                return JSONResponse({"detail": "missing or invalid bearer token"}, status_code=401)
            if not authorize(principal, request.method, request.url.path):
                return JSONResponse(
                    {"detail": "this token is not allowed to call that route"}, status_code=403
                )
            request.state.principal = principal  # let handlers scope writes to the caller
            return await call_next(request)

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    _mount_instances(app, registry, verify)
    _mount_token_routes(app, registry, verify)
    _mount_command_queue(app, registry)
    _mount_aggregation(app, agg)
    _mount_observability(app, observability or {})
    _mount_artifacts(app, arts)
    _mount_proposals(app, props, arts)
    _mount_deploy(app, registry, arts, agg, deploy)
    return app


def _mount_deploy(
    app: FastAPI,
    registry: SqliteRegistry,
    artifacts: ArtifactStore,
    agg: AggregationStore,
    deploy: DeployFn,
) -> None:
    """Governed deploy of a published registry version onto an instance (doc 15 / doc 17 step 7).

    Operator-only (legislative; the version was already human-approved to publish). Mode A pushes to
    the instance's serve /api; Mode B enqueues a `deploy` command for the connector. Always audited.
    """

    @app.post("/instances/{instance_id}/deploy")
    async def deploy_artifact(
        instance_id: str, req: DeployRequest, request: Request
    ) -> dict[str, Any]:
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if req.kind not in DEPLOYABLE:
            raise HTTPException(
                400, f"kind '{req.kind}' is not deployable — use {'/'.join(DEPLOYABLE)}"
            )
        ver = artifacts.get_version(req.kind, req.artifact_id, req.version)
        if ver is None:
            raise HTTPException(404, f"no such version {req.kind}/{req.artifact_id}@{req.version}")

        # Record the registry-intended version (the deployment), then push.
        artifacts.set_deployment(instance_id, req.kind, req.artifact_id, req.version)
        content = ver["content"]

        if inst.connection == "direct":
            try:
                result = await deploy(
                    inst.endpoint, inst.token_ref, req.kind, req.artifact_id, content
                )
            except DeployError as exc:
                raise HTTPException(502, f"deploy failed: {exc}") from exc
            outcome: dict[str, Any] = {"mode": "direct", "result": result}
        else:
            cmd = registry.enqueue(
                instance_id, "deploy", {"kind": req.kind, "id": req.artifact_id, "body": content}
            )
            outcome = {"mode": "poll", "command_id": cmd.cmd_id}

        principal = getattr(request.state, "principal", None)
        by = (getattr(principal, "subject", None) or "") if principal else ""
        agg.ingest(
            instance_id,
            "audit",
            [
                {
                    "id": uuid4().hex,
                    "ts": datetime.now(UTC).isoformat(),
                    "action": "artifact.deploy",
                    "kind": req.kind,
                    "artifact_id": req.artifact_id,
                    "version": req.version,
                    "by": by,
                }
            ],
        )
        return {"status": "ok", "version": req.version, **outcome}


def _mount_instances(app: FastAPI, registry: SqliteRegistry, verify: VerifyFn) -> None:
    """Instance registry CRUD + enrollment + heartbeat (doc 13)."""

    @app.post("/instances")
    async def enroll(req: EnrollRequest) -> dict[str, Any]:
        if req.connection not in ("direct", "poll"):
            raise HTTPException(400, "connection must be 'direct' or 'poll'")
        if tier_rank(req.tier) < 0:
            raise HTTPException(400, "tier must be 'read', 'run', or 'admin'")
        inst = Instance(
            id=uuid4().hex[:12],
            name=req.name,
            endpoint=req.endpoint,
            token_ref=req.token_ref,
            connection=req.connection,  # type: ignore[arg-type]
            tier=req.tier.strip().lower(),
        )
        # Mode A with a token already supplied: verify by pulling the instance's capabilities.
        # Without a token (mint-then-install flow) or in Mode B (the instance polls us), enroll
        # unverified — verify later via POST /verify (Mode A) or the first heartbeat/poll (Mode B).
        if req.connection == "direct" and req.token_ref:
            try:
                caps = await verify(req.endpoint, req.token_ref)
            except ConnectorError as exc:
                raise HTTPException(502, f"enrollment verification failed: {exc}") from exc
            inst.capabilities = caps
            inst.schema_version = str(caps.get("schema_version", ""))
            inst.health = "healthy"
        registry.add(inst)
        return inst.public_dict()

    @app.get("/instances")
    async def list_instances() -> list[dict[str, Any]]:
        return [i.public_dict() for i in registry.list_all()]

    @app.get("/instances/{instance_id}")
    async def get_instance(instance_id: str) -> dict[str, Any]:
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        return inst.public_dict()

    @app.delete("/instances/{instance_id}")
    async def delete_instance(instance_id: str) -> dict[str, Any]:
        if not registry.delete(instance_id):
            raise HTTPException(404, "instance not found")
        return {"deleted": instance_id}

    @app.post("/instances/{instance_id}/heartbeat")
    async def heartbeat(instance_id: str, req: HeartbeatRequest) -> dict[str, str]:
        if registry.get(instance_id) is None:
            raise HTTPException(404, "instance not found")
        registry.update_health(
            instance_id,
            health="healthy",
            schema_version=req.schema_version,
            capabilities=req.capabilities,
        )
        return {"status": "ok"}


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


def _mount_artifacts(app: FastAPI, store: ArtifactStore) -> None:
    """Artifact registry: versioned artifacts + provenance, deployments, drift (doc 15)."""

    def _check_kind(kind: str) -> None:
        if kind not in ARTIFACT_KINDS:
            raise HTTPException(404, f"unknown kind '{kind}' — use {'/'.join(ARTIFACT_KINDS)}")

    @app.post("/artifacts/{kind}/{artifact_id}/versions")
    async def register_version(
        kind: str, artifact_id: str, req: RegisterVersionRequest
    ) -> dict[str, Any]:
        _check_kind(kind)
        return store.register_version(
            kind,
            artifact_id,
            content=req.content,
            authored_by=req.authored_by,
            schema_version=req.schema_version,
            version=req.version,
        )

    @app.get("/artifacts")
    async def list_artifacts() -> list[dict[str, Any]]:
        return store.list_artifacts()

    @app.get("/artifacts/{kind}/{artifact_id}/versions")
    async def list_versions(kind: str, artifact_id: str) -> list[dict[str, Any]]:
        _check_kind(kind)
        return store.list_versions(kind, artifact_id)

    @app.get("/artifacts/{kind}/{artifact_id}/versions/{version}")
    async def get_version(kind: str, artifact_id: str, version: str) -> dict[str, Any]:
        _check_kind(kind)
        found = store.get_version(kind, artifact_id, version)
        if found is None:
            raise HTTPException(404, "version not found")
        return found

    @app.put("/instances/{instance_id}/deployments/{kind}/{artifact_id}")
    async def set_deployment(
        instance_id: str, kind: str, artifact_id: str, req: DeploymentRequest
    ) -> dict[str, str]:
        _check_kind(kind)
        try:
            store.set_deployment(instance_id, kind, artifact_id, req.version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"status": "ok"}

    @app.get("/instances/{instance_id}/deployments")
    async def list_deployments(instance_id: str) -> list[dict[str, Any]]:
        return store.list_deployments(instance_id)

    @app.post("/instances/{instance_id}/artifacts/report")
    async def report_artifacts(instance_id: str, req: ReportArtifactsRequest) -> dict[str, int]:
        return {"reported": store.report(instance_id, req.records)}

    @app.get("/instances/{instance_id}/drift")
    async def drift(instance_id: str) -> list[dict[str, Any]]:
        return store.drift(instance_id)


def _mount_observability(app: FastAPI, config: dict[str, str]) -> None:
    """Expose the configured collector + dashboard URLs for the fleet UI to link out (doc 14)."""

    @app.get("/observability")
    async def observability() -> dict[str, str]:
        # The collector endpoint instances send OTLP to; the Jaeger/Grafana URLs the UI deep-links.
        return {
            "collector_endpoint": config.get("collector_endpoint", ""),
            "jaeger_url": config.get("jaeger_url", ""),
            "grafana_url": config.get("grafana_url", ""),
        }


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
    async def audit(limit: int = 100) -> list[dict[str, Any]]:
        return agg.recent_audit(limit)


def _mount_token_routes(app: FastAPI, registry: SqliteRegistry, verify: VerifyFn) -> None:
    """Per-instance token minting + Mode A re-verification (doc 12 §6, doc 13 enrollment)."""

    @app.post("/instances/{instance_id}/mint-token")
    async def mint_instance_token(instance_id: str, req: MintTokenRequest) -> dict[str, Any]:
        """Mint a per-instance serve token. The secret is returned ONCE and never stored."""
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        tier = (req.tier or inst.tier).strip().lower()
        if tier_rank(tier) < 0:
            raise HTTPException(400, "tier must be 'read', 'run', or 'admin'")
        try:
            minted = mint_token(instance_id, tier=tier, client_name=req.client_name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        # Persist the reference + fingerprint + metadata only — never the token value.
        registry.set_token(
            instance_id,
            token_ref=minted.key_ref,
            fingerprint=minted.fingerprint,
            token_hash=minted.token_hash,
            tier=tier,
            minted_at=datetime.now(UTC).isoformat(),
        )
        return {
            "token": minted.token,  # shown once
            "client_id": minted.client_id,
            "client_name": minted.client_name,
            "tier": minted.tier,
            "key_ref": minted.key_ref,
            "fingerprint": minted.fingerprint,
            "server_auth_snippet": minted.server_auth_snippet(),
            "instructions": (
                f"Set {minted.key_ref.removeprefix('env:')}=<token> on the instance host and on "
                "the panel host, paste server_auth_snippet under the instance workspace.yaml, then "
                "reload serve and call POST /instances/{id}/verify."
            ),
        }

    @app.post("/instances/{instance_id}/verify")
    async def verify_instance(instance_id: str) -> dict[str, Any]:
        """Re-run the Mode A pull-verify against the instance's stored token_ref."""
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if inst.connection != "direct":
            raise HTTPException(409, "verify pulls capabilities — only valid for direct (Mode A)")
        try:
            caps = await verify(inst.endpoint, inst.token_ref)
        except ConnectorError as exc:
            registry.update_health(instance_id, health="unreachable")
            raise HTTPException(502, f"verification failed: {exc}") from exc
        registry.update_health(
            instance_id,
            health="healthy",
            schema_version=str(caps.get("schema_version", "")),
            capabilities=caps,
        )
        updated = registry.get(instance_id)
        assert updated is not None
        return updated.public_dict()


def _mount_command_queue(app: FastAPI, registry: SqliteRegistry) -> None:
    """Mode B command-queue routes (doc 13 §"Mode B — poll connector")."""

    @app.post("/instances/{instance_id}/commands")
    async def enqueue_command(instance_id: str, req: EnqueueCommandRequest) -> dict[str, Any]:
        """Operator/panel enqueues a command for a poll-connected instance."""
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if inst.connection != "poll":
            raise HTTPException(409, "commands are only enqueued for poll-mode (Mode B) instances")
        if not is_known_verb(req.verb):
            raise HTTPException(400, f"unknown verb: {req.verb}")
        if not verb_within_tier(req.verb, inst.tier):
            raise HTTPException(
                403, f"verb '{req.verb}' exceeds the instance's granted tier '{inst.tier}'"
            )
        cmd = registry.enqueue(instance_id, req.verb, req.args)
        return cmd.public_dict()

    @app.get("/instances/{instance_id}/commands")
    async def list_commands(instance_id: str) -> list[dict[str, Any]]:
        if registry.get(instance_id) is None:
            raise HTTPException(404, "instance not found")
        return [c.public_dict() for c in registry.list_commands(instance_id)]

    @app.post("/instances/{instance_id}/poll")
    async def poll(instance_id: str, req: PollRequest) -> dict[str, Any]:
        """Connector long-poll: folds heartbeat + drains the command queue (short-poll for now)."""
        if registry.get(instance_id) is None:
            raise HTTPException(404, "instance not found")
        # Heartbeat + capability refresh fold into the poll (no separate heartbeat in Mode B).
        registry.update_health(
            instance_id,
            health="healthy",
            schema_version=req.schema_version,
            capabilities=req.capabilities,
        )
        cmds = registry.claim_queued(instance_id)
        return {"commands": [c.dispatch_dict() for c in cmds]}

    @app.post("/instances/{instance_id}/commands/{cmd_id}/result")
    async def command_result(
        instance_id: str, cmd_id: str, req: CommandResultRequest
    ) -> dict[str, Any]:
        if req.status not in ("done", "error"):
            raise HTTPException(400, "status must be 'done' or 'error'")
        cmd = registry.get_command(cmd_id)
        if cmd is None or cmd.instance_id != instance_id:
            raise HTTPException(404, "command not found")
        recorded = registry.record_result(
            cmd_id,
            status=req.status,  # type: ignore[arg-type]
            output=req.output,
            error=req.error,
        )
        # Idempotent: a repeated result (at-least-once delivery) is accepted, not an error.
        return {"status": "ok", "recorded": recorded}
