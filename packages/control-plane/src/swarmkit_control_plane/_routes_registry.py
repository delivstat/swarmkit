"""Instance registry / token / command-queue / config routes (docs 12-14)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from swarmkit_control_plane._connector import ConnectorError
from swarmkit_control_plane._fntypes import (
    AuthorFn,
    JobsFn,
    StateFn,
    VerifyFn,
)
from swarmkit_control_plane._fntypes import extract_artifact as _extract_artifact
from swarmkit_control_plane._models import Instance
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._schemas import (
    AuthorRequest,
    CommandResultRequest,
    EnqueueCommandRequest,
    EnrollRequest,
    HeartbeatRequest,
    MintTokenRequest,
    PollRequest,
)
from swarmkit_control_plane._state_store import InstanceStateStore
from swarmkit_control_plane._tokens import mint_token
from swarmkit_control_plane._verbs import is_known_verb, tier_rank, verb_within_tier


def _mount_config(app: FastAPI, config: dict[str, Any]) -> None:
    """Read-only panel config for the fleet UI's Settings page (no secrets — only flags + URLs)."""

    @app.get("/config")
    async def get_config() -> dict[str, Any]:
        return config


def _mount_instances(
    app: FastAPI, registry: SqliteRegistry, verify: VerifyFn, jobs: JobsFn, author: AuthorFn
) -> None:
    """Instance registry CRUD + enrollment + heartbeat + federated live jobs (docs 13, 14)."""

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

    @app.get("/instances/{instance_id}/jobs")
    async def instance_jobs(instance_id: str) -> list[dict[str, Any]]:
        """Federated live query of an instance's current jobs (not stored — pulled on demand)."""
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if inst.connection != "direct":
            # The panel can't reach a NAT'd poll instance directly; a live query needs Mode A.
            raise HTTPException(409, "live jobs require a directly-reachable (Mode A) instance")
        try:
            return await jobs(inst.endpoint, inst.token_ref)
        except ConnectorError as exc:
            raise HTTPException(502, f"could not query jobs: {exc}") from exc

    @app.post("/instances/{instance_id}/author")
    async def author_turn(instance_id: str, req: AuthorRequest) -> dict[str, Any]:
        """Conversational authoring (doc 16): drive the instance's authoring swarm for one
        turn and return its reply, plus any drafted artifact the swarm emitted (a JSON
        {kind, id, content} envelope) so the UI can preview it and propose it for approval.
        Operator-only (authorize denies connector tokens); Mode A only — driving a swarm
        needs a directly-reachable instance."""
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if inst.connection != "direct":
            raise HTTPException(409, "authoring requires a directly-reachable (Mode A) instance")
        try:
            result = await author(inst.endpoint, inst.token_ref, req.topology, req.message)
        except ConnectorError as exc:
            raise HTTPException(502, f"authoring run failed: {exc}") from exc
        reply = result.get("reply") or ""
        return {"reply": reply, "artifact": _extract_artifact(reply)}


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


def _mount_state(
    app: FastAPI,
    registry: SqliteRegistry,
    state_store: InstanceStateStore,
    fetch_state: StateFn,
) -> None:
    """Observed-state cache routes (fleet enrollment Phase 1, design 19).

    ``/sync`` pulls the instance's full state (Mode A) and caches it; ``/state`` returns the cached
    snapshot — so an instance's inventory stays inspectable even when it's **offline**.
    """

    @app.post("/instances/{instance_id}/sync")
    async def sync_state(instance_id: str) -> dict[str, Any]:
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if inst.connection != "direct":
            raise HTTPException(
                409, "sync pulls /fleet/state — only valid for direct (Mode A) instances"
            )
        try:
            state = await fetch_state(inst.endpoint, inst.token_ref)
        except ConnectorError as exc:
            registry.update_health(instance_id, health="unreachable")
            raise HTTPException(502, f"state sync failed: {exc}") from exc
        synced_at = state_store.put(instance_id, state)
        arts = state.get("artifacts", {}) if isinstance(state, dict) else {}
        return {
            "instance_id": instance_id,
            "synced_at": synced_at,
            "counts": {kind: len(items) for kind, items in arts.items()},
        }

    @app.get("/instances/{instance_id}/state")
    async def get_state(instance_id: str) -> dict[str, Any]:
        if registry.get(instance_id) is None:
            raise HTTPException(404, "instance not found")
        cached = state_store.get(instance_id)
        if cached is None:
            raise HTTPException(404, "no state cached yet — POST /instances/{id}/sync first")
        return cached  # {state, synced_at} — served from cache, works offline


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
