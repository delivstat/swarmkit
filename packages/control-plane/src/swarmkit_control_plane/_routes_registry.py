"""Instance registry / token / command-queue / config routes (docs 12-14)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._connector import ConnectorError
from swarmkit_control_plane._credential_store import CredentialStore
from swarmkit_control_plane._fntypes import (
    AuthorFn,
    JobsFn,
    LeaveFn,
    RefreshFn,
    RegisterFn,
    StateFn,
    VerifyFn,
)
from swarmkit_control_plane._fntypes import extract_artifact as _extract_artifact
from swarmkit_control_plane._join_code_store import DEFAULT_JOIN_TTL_S, JoinCodeStore
from swarmkit_control_plane._models import Instance
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._schemas import (
    AdoptRequest,
    AuthorRequest,
    CommandResultRequest,
    EnqueueCommandRequest,
    EnrollRequest,
    HeartbeatRequest,
    JoinCodeRequest,
    JoinRequest,
    MintTokenRequest,
    PollRequest,
    RegisterInstanceRequest,
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


#: InstanceState artifact kind → the collection key it lives under in the cached export.
_ADOPT_COLLECTIONS: dict[str, str] = {
    "topology": "topologies",
    "skill": "skills",
    "archetype": "archetypes",
    "trigger": "triggers",
}


def _find_cached_artifact(
    state: dict[str, Any], kind: str, artifact_id: str
) -> dict[str, Any] | None:
    """Locate a ``{id, version, content_hash, content}`` entry in a cached ``InstanceState``, or
    None if *kind* isn't an adoptable collection or the id isn't present."""
    collection = _ADOPT_COLLECTIONS.get(kind)
    if collection is None:
        return None
    artifacts = state.get("artifacts", {}) if isinstance(state, dict) else {}
    for entry in artifacts.get(collection, []) or []:
        if isinstance(entry, dict) and entry.get("id") == artifact_id:
            return entry
    return None


def _mount_state(
    app: FastAPI,
    registry: SqliteRegistry,
    state_store: InstanceStateStore,
    artifacts: ArtifactStore,
    fetch_state: StateFn,
) -> None:
    """Observed-state cache routes (fleet enrollment Phase 1, design 19) + adopt (Phase 3, doc 20).

    ``/sync`` pulls the instance's full state (Mode A) and caches it; ``/state`` returns the cached
    snapshot — so an instance's inventory stays inspectable even when it's **offline**. ``/adopt``
    promotes one cached artifact into the deployable registry (the observed snapshot is kept
    separate from the registry — adoption is the explicit bridge).
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

    @app.post("/instances/{instance_id}/adopt")
    async def adopt_artifact(instance_id: str, req: AdoptRequest) -> dict[str, Any]:
        """Promote a cached observed artifact into the deployable registry (design 20). Reads the
        artifact's content from the last-synced ``InstanceState`` and registers it as a registry
        version (idempotent on ``content_hash``), with provenance recording the source instance +
        the snapshot's ``synced_at`` — so an operator can see an artifact came from instance X
        before deploying it fleet-wide."""
        if registry.get(instance_id) is None:
            raise HTTPException(404, "instance not found")
        if req.kind not in _ADOPT_COLLECTIONS:
            raise HTTPException(400, f"kind '{req.kind}' is not adoptable")
        cached = state_store.get(instance_id)
        if cached is None:
            raise HTTPException(409, "no state cached yet — POST /instances/{id}/sync first")
        entry = _find_cached_artifact(cached["state"], req.kind, req.artifact_id)
        if entry is None:
            raise HTTPException(404, f"{req.kind} '{req.artifact_id}' is not in the cached state")
        state = cached["state"]
        published = artifacts.register_version(
            req.kind,
            req.artifact_id,
            content=entry.get("content"),
            authored_by=f"adopted:instance/{instance_id}@{cached['synced_at']}",
            schema_version=str(state.get("schema_version", "")) if isinstance(state, dict) else "",
        )
        return {
            "kind": req.kind,
            "artifact_id": req.artifact_id,
            "version": published["version"],
            "content_hash": published["content_hash"],
            "adopted_from": instance_id,
            "synced_at": cached["synced_at"],
        }


#: The panel's own fleet identity, presented to instances at register (overridable per request).
DEFAULT_FLEET_ID = "swarmkit-fleet"


def _mount_membership(
    app: FastAPI,
    registry: SqliteRegistry,
    cred_store: CredentialStore,
    leave_fn: LeaveFn,
) -> None:
    """Surface + leave the fleet relationship **this** panel holds for an instance (design 20).

    Multi-fleet visibility is panel-perspective: a fleet sees and manages only its own membership
    (from its encrypted store); it does not enumerate other fleets the instance may belong to — that
    is the instance owner's serve-side view."""

    @app.get("/instances/{instance_id}/membership")
    async def get_membership(instance_id: str) -> dict[str, Any]:
        """This fleet's membership metadata — fleet id, scope, fingerprint, created (no secret)."""
        if registry.get(instance_id) is None:
            raise HTTPException(404, "instance not found")
        meta = cred_store.get_metadata(instance_id)
        if meta is None:
            raise HTTPException(404, "this fleet holds no membership for the instance")
        return meta

    @app.delete("/instances/{instance_id}/membership")
    async def leave_fleet(instance_id: str) -> dict[str, Any]:
        """Leave the fleet: revoke this panel's membership on the instance with the membership key
        itself (self-leave, design 19), then forget the stored credential."""
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        key = cred_store.get_secret(instance_id)
        meta = cred_store.get_metadata(instance_id)
        if key is None or meta is None:
            raise HTTPException(404, "this fleet holds no membership for the instance")
        try:
            await leave_fn(inst.endpoint, key, meta["membership_id"])
        except ConnectorError as exc:
            raise HTTPException(502, f"leave failed: {exc}") from exc
        cred_store.delete(instance_id)
        return {"left": meta["fleet_id"], "membership_id": meta["membership_id"]}


def _mount_register(
    app: FastAPI,
    registry: SqliteRegistry,
    state_store: InstanceStateStore,
    cred_store: CredentialStore,
    register_fn: RegisterFn,
    refresh_fn: RefreshFn,
) -> None:
    """The register handshake (design 19, Phase 2): the panel joins an instance with a one-time
    enrollment token, receives a scoped membership credential (stored encrypted) + the instance's
    full state (cached), in one call. ``/refresh`` rotates the stored credential."""

    @app.post("/instances/{instance_id}/register")
    async def register_instance(instance_id: str, req: RegisterInstanceRequest) -> dict[str, Any]:
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if inst.connection != "direct":
            raise HTTPException(
                409, "register pulls /fleet/register — only valid for direct (Mode A)"
            )
        fleet_id = req.fleet_id or DEFAULT_FLEET_ID
        try:
            result = await register_fn(
                inst.endpoint, req.enroll_token, fleet_id, req.requested_scope
            )
        except ConnectorError as exc:
            raise HTTPException(502, f"register failed: {exc}") from exc

        cred = result.get("credential", {})
        cred_store.put_credential(
            instance_id,
            membership_id=result["membership_id"],
            fleet_id=fleet_id,
            scope=str(cred.get("scope", "")),
            fingerprint=str(cred.get("fingerprint", "")),
            secret=str(cred.get("value", "")),  # encrypted at rest by the store
        )
        state = result.get("instance_state", {}) or {}
        synced_at = state_store.put(instance_id, state)
        registry.update_health(
            instance_id, health="healthy", schema_version=str(state.get("schema_version", ""))
        )
        arts = state.get("artifacts", {}) if isinstance(state, dict) else {}
        return {
            "membership_id": result["membership_id"],
            "scope": cred.get("scope", ""),
            "fingerprint": cred.get("fingerprint", ""),
            "synced_at": synced_at,
            "counts": {kind: len(items) for kind, items in arts.items()},
        }

    @app.post("/instances/{instance_id}/refresh")
    async def refresh_instance(instance_id: str) -> dict[str, Any]:
        """Rotate the stored membership credential: decrypt it, use it against the instance's
        /fleet/refresh, and re-store the rotated key encrypted (design 19)."""
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if inst.connection != "direct":
            raise HTTPException(
                409, "refresh calls /fleet/refresh — only valid for direct (Mode A)"
            )
        key = cred_store.get_secret(instance_id)
        meta = cred_store.get_metadata(instance_id)
        if key is None or meta is None:
            raise HTTPException(400, "no membership credential for this instance — register first")
        try:
            result = await refresh_fn(inst.endpoint, key)
        except ConnectorError as exc:
            raise HTTPException(502, f"refresh failed: {exc}") from exc
        cred = result.get("credential", {})
        cred_store.put_credential(
            instance_id,
            membership_id=result.get("membership_id", meta["membership_id"]),
            fleet_id=meta["fleet_id"],
            scope=str(cred.get("scope", meta["scope"])),
            fingerprint=str(cred.get("fingerprint", "")),
            secret=str(cred.get("value", "")),  # re-encrypted at rest
        )
        return {
            "membership_id": result.get("membership_id", meta["membership_id"]),
            "scope": cred.get("scope", meta["scope"]),
            "fingerprint": cred.get("fingerprint", ""),
        }


def _mount_join(
    app: FastAPI,
    registry: SqliteRegistry,
    join_store: JoinCodeStore,
    state_store: InstanceStateStore,
) -> None:
    """Mode B (instance-initiated) join (design 19). The panel can't reach a NAT'd instance, so the
    handshake inverts: the operator mints a one-time join code, the edge calls ``POST /fleet/join``
    with it + its full state, and the panel creates the (poll-mode) Instance, issues the
    connector→panel credential (shown once), and caches the state — mirroring Mode A register."""

    @app.post("/fleet/join-code")
    async def mint_join_code(req: JoinCodeRequest) -> dict[str, Any]:
        """Operator action: mint a one-time join code to hand to an edge instance (the fleet UI's
        'enroll (poll)'). Operator-gated at the seam — a connector token can't mint codes."""
        tier = (req.tier or "read").strip().lower()
        if tier_rank(tier) < 0:
            raise HTTPException(400, "tier must be 'read', 'run', or 'admin'")
        ttl = req.ttl_seconds if req.ttl_seconds and req.ttl_seconds > 0 else DEFAULT_JOIN_TTL_S
        code = join_store.mint(name=req.name, endpoint=req.endpoint, tier=tier, ttl_seconds=ttl)
        return {"join_code": code, "tier": tier, "expires_in": ttl}

    @app.post("/fleet/join")
    async def join(req: JoinRequest) -> dict[str, Any]:
        """Instance-initiated join. **Auth-exempt at the seam** — the join code *is* the auth,
        consumed single-use here (design 19, Mode B). Creates a poll-mode Instance, mints its
        connector→panel token (returned once), caches the presented state, and marks it healthy."""
        consumed = join_store.consume(req.join_code)
        if consumed is None:
            raise HTTPException(401, "invalid, expired, or already-used join code")
        identity = req.instance_identity or {}
        name = str(identity.get("name") or consumed["name"] or "edge-instance")
        endpoint = str(identity.get("endpoint") or consumed["endpoint"] or "")
        tier = str(consumed["tier"])
        inst = Instance(
            id=uuid4().hex[:12], name=name, endpoint=endpoint, connection="poll", tier=tier
        )
        registry.add(inst)
        # The credential the connector polls with: a per-instance token, authenticated by hash
        # (get_by_token_hash) like an operator-minted one. Shown once; only its hash is kept.
        minted = mint_token(inst.id, tier=tier, client_name=name)
        registry.set_token(
            inst.id,
            token_ref=minted.key_ref,
            fingerprint=minted.fingerprint,
            token_hash=minted.token_hash,
            tier=tier,
            minted_at=datetime.now(UTC).isoformat(),
        )
        state = req.instance_state or {}
        synced_at = state_store.put(inst.id, state)
        registry.update_health(
            inst.id, health="healthy", schema_version=str(state.get("schema_version", ""))
        )
        arts = state.get("artifacts", {}) if isinstance(state, dict) else {}
        return {
            "instance_id": inst.id,
            "membership_id": uuid4().hex[:12],  # the instance's to keep (its record of this join)
            "credential": {
                "type": "api_key",
                "value": minted.token,  # shown once; the connector polls the panel with this
                "tier": tier,
                "key_ref": minted.key_ref,
                "fingerprint": minted.fingerprint,
            },
            "synced_at": synced_at,
            "counts": {kind: len(items) for kind, items in arts.items()},
        }


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
