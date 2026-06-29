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

from swarmkit_control_plane._auth import authenticate, authorize
from swarmkit_control_plane._connector import ConnectorError, fetch_capabilities
from swarmkit_control_plane._models import Instance
from swarmkit_control_plane._oidc import OidcVerifier
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._tokens import mint_token
from swarmkit_control_plane._verbs import is_known_verb, tier_rank, verb_within_tier

VerifyFn = Callable[[str, str], Awaitable[dict[str, Any]]]


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


def create_app(
    registry: SqliteRegistry,
    *,
    verify: VerifyFn = fetch_capabilities,
    cors_origins: list[str] | None = None,
    operator_tokens: list[str] | None = None,
    oidc: OidcVerifier | None = None,
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

    _mount_token_routes(app, registry, verify)
    _mount_command_queue(app, registry)
    return app


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
