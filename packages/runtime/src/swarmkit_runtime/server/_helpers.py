"""Shared serve helpers — runtime accessor, route→scope mapping, capability advertisement,
access-audit, and webhook-signature validation. Split out so the route modules and the app
factory import them without an ``_app`` ⇄ routes cycle."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import yaml
from fastapi import HTTPException, Request

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.connect import DEPLOY_PLURAL
from swarmkit_runtime.fleet import (
    ACTOR_ASSERTION_TTL_S,
    actor_message,
    deploy_message,
    scope_covers,
    verify_signature,
)
from swarmkit_runtime.server._a2a import remote_agent_rows
from swarmkit_runtime.triggers._webhook import default_auth_header, validate_webhook_auth

logger = logging.getLogger("swarmkit.server")


def _webhook_secret(request: Request, ref: str) -> str:
    """The signing secret behind a trigger's `credentials_ref`.

    A `credentials_ref` is a name in the workspace `credentials` block — the same reference an
    MCP server's `{credential.<name>}` uses — resolved through the credential service. It was read
    as an environment-variable NAME instead (`os.environ["github-webhook-secret"]`), so a trigger
    written the documented way was refused with 503 "not set in the environment". A ref that is
    not a declared credential still falls back to the environment, for the older `secret_ref`
    spelling.
    """
    app = getattr(request, "app", None)
    runtime = getattr(getattr(app, "state", None), "runtime", None)
    service = getattr(runtime, "_credential_service", None)
    if service is not None:
        try:
            return str(service.resolve_sync(ref))
        except Exception:
            logger.debug(
                "credentials_ref %r is not a workspace credential; trying the environment", ref
            )
    return os.environ.get(ref, "")


def _check_webhook_signature(
    request: Request,
    raw_body: bytes,
    topology_name: str,
) -> None:
    """Validate HMAC signature for webhook triggers that have auth configured.

    Raises HTTPException(401) when a matching trigger config requires auth and
    the signature is absent or incorrect.  No-ops when no trigger requires auth.
    """
    trigger_configs: list[dict[str, Any]] = getattr(request.app.state, "trigger_configs", [])
    for tc in trigger_configs:
        if tc.get("type") != "webhook":
            continue
        if topology_name not in tc.get("targets", []):
            continue
        config = tc.get("config") or {}
        auth = config.get("auth") or {}
        secret_ref = auth.get("credentials_ref") or config.get("secret_ref")
        if not secret_ref:
            continue
        secret = _webhook_secret(request, str(secret_ref))
        if not secret:
            # FAIL CLOSED. Skipping validation because the secret is missing accepts unsigned
            # requests, and at runtime that is indistinguishable from a correctly configured
            # trigger — the declared protection is simply absent. Neither `swarmkit serve` nor
            # `swarmkit orchestrator` loads a .env file, so an unexported variable is the default
            # state, which made this the common case rather than the edge case.
            logger.error(
                "Webhook trigger secret_ref=%r is not present in the environment; "
                "refusing the request for topology=%r (export it, or remove the auth block)",
                secret_ref,
                topology_name,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    f"webhook trigger declares credentials_ref={secret_ref!r} but it is not set "
                    "in the environment; refusing to accept unsigned requests"
                ),
            )
        method = str(auth.get("method", "hmac"))
        header_name = auth.get("header", default_auth_header(method))
        sig = request.headers.get(header_name, "")
        if not validate_webhook_auth(method, sig, raw_body, secret):
            logger.warning(
                "Webhook signature validation failed for topology=%r",
                topology_name,
            )
            raise HTTPException(
                status_code=401,
                detail="Invalid webhook signature",
            )
        break


def _check_pipeline_webhook_signature(
    request: Request,
    raw_body: bytes,
    trigger_config: dict[str, Any],
) -> None:
    """Validate the HMAC signature of a pipeline-event webhook, addressed by *trigger_config*.

    The pipeline webhook path (``POST /hooks/{trigger_id}``) is resolved by trigger id, not by a
    topology target, so it cannot reuse the topology-target signature match — but it reuses the
    same HMAC primitive (``validate_webhook_signature``). Raises HTTPException(401) when the
    trigger declares ``config.auth`` and the signature is absent or wrong; no-ops when the trigger
    configures no auth or its secret is not present in the environment (same lenient posture as the
    topology path, so a mis-set secret ref does not hard-fail startup)."""
    config = trigger_config.get("config") or {}
    auth = config.get("auth") or {}
    secret_ref = auth.get("credentials_ref") or config.get("secret_ref")
    if not secret_ref:
        return
    secret = _webhook_secret(request, str(secret_ref))
    if not secret:
        logger.error(
            "Pipeline webhook trigger secret_ref=%r is not present in the environment; "
            "refusing the request for trigger=%r (export it, or remove the auth block)",
            secret_ref,
            trigger_config.get("id"),
        )
        raise HTTPException(
            status_code=503,
            detail=(
                f"webhook trigger declares credentials_ref={secret_ref!r} but it is not set in "
                "the environment; refusing to accept unsigned requests"
            ),
        )
    method = str(auth.get("method", "hmac"))
    header_name = auth.get("header", default_auth_header(method))
    sig = request.headers.get(header_name, "")
    if not validate_webhook_auth(method, sig, raw_body, secret):
        logger.warning(
            "Webhook signature validation failed for pipeline trigger=%r",
            trigger_config.get("id"),
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


def _required_action(method: str, path: str) -> str | None:  # noqa: PLR0911
    """The serve:* tier action a route requires, or None for auth-exempt.

    read = all GETs (observe); admin = artifact mutation (/api/* writes) + canary
    promote/rollback; run = every other write (run/hooks/conversations/mcp). See
    design/details/control-plane/12-auth.md §4.
    """
    if path == "/health":
        return None
    # The Agent Card is discovery: a remote agent reads it before it holds any credential, and the
    # card itself says which bearer scheme the task API wants (design/details/a2a-interop.md).
    if path == "/.well-known/agent-card.json":
        return None
    # Minting a fleet enrollment token (a join code) is an admin/human action — and this is what
    # makes a `manage`-scope join human-issued (design 19). /fleet/register is auth-exempt at the
    # seam (it authenticates with the one-time enrollment token itself; see the middleware).
    if path == "/fleet/enroll-token":
        return "admin"
    # Listing / ejecting memberships + unpinning a fleet identity are the instance owner's call
    # (admin). /fleet/refresh is exempt at the seam (it authenticates with the current membership
    # key; see the middleware).
    if path.startswith("/fleet/membership") or path.startswith("/fleet/identity"):
        return "admin"
    # Delta-sync body fetch is a content read; it uses POST only to carry the ref list.
    if path == "/fleet/state/artifacts":
        return "read"
    # The operator inventory of stored tokens answers "who has connected what" — a roster, and in
    # a multi-user deployment that list is sometimes more sensitive than any single connection.
    # Admin only. A caller's own connection state lives at /api/oauth/my-credentials, which derives
    # the owner from the authenticated identity and can name nobody else
    # (per-caller-credential-delegation.md).
    if method.upper() == "GET" and path == "/api/oauth/credentials":
        return "admin"
    # Disconnecting is owner-scoped in the handler: it can only remove the caller's own token. A
    # person revoking their own access is ordinary authenticated work, not an administrative act,
    # and requiring admin would mean nobody could disconnect the account they connected.
    if method.upper() == "DELETE" and path.startswith("/api/oauth/credentials/"):
        return "run"
    if method.upper() == "GET":
        return "read"
    if path.startswith("/api/"):
        return "admin"
    # Any canary mutation — promote, rollback, or start a new canary (design 26) — is admin.
    if path.startswith("/canary/"):
        return "admin"
    return "run"


def _pkg_version(name: str) -> str:
    """Installed package version, or 'unknown'."""
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def _enum_value(obj: Any, attr: str, default: str = "") -> str:
    """Read a (possibly Enum-valued) attribute as its string, or default."""
    val = getattr(obj, attr, None)
    val = getattr(val, "value", val)  # pydantic Enum -> str
    return val if isinstance(val, str) else default


def _build_capabilities(rt: Any) -> dict[str, Any]:
    """Advertise what this instance can do — consumed by the control plane at enroll/refresh.

    See design/details/control-plane/13-connector-registry.md.
    """
    ws = rt.workspace
    raw = ws.raw
    server_raw = getattr(raw, "server", None)
    canary = getattr(server_raw, "canary", None)
    return {
        "serve_version": _pkg_version("swarmkit-runtime"),
        "schema_version": _pkg_version("swarmkit-schema"),
        "workspace_id": str(raw.metadata.id),
        "workspace_name": str(getattr(raw.metadata, "name", "") or ""),
        # `name@version` keys are canary routing aliases of a topology the list already names;
        # a fleet counting topologies must not count them twice (and /fleet/state cannot read them).
        "topologies": sorted(k for k in ws.topologies if "@" not in k),
        "model_providers": rt.provider_registry.provider_ids,
        "governance_provider": _enum_value(getattr(raw, "governance", None), "provider", "mock"),
        "features": {
            "auth": _enum_value(getattr(server_raw, "auth", None), "provider", "none"),
            "compression": _enum_value(getattr(raw, "context_compression", None), "backend", "off"),
            "canary": bool(getattr(canary, "routes", None)),
            "a2a": bool(getattr(getattr(server_raw, "a2a", None), "enabled", False)),
        },
    }


def _content_hash(content: Any) -> str:
    """SHA-256 of artifact content — the *same* canonicalisation the panel's artifact registry uses
    (sorted-keys compact JSON for dicts), so an adopted artifact's hash lines up with the fleet's
    (design/details/control-plane/15-artifact-registry.md)."""
    text = (
        json.dumps(content, sort_keys=True, separators=(",", ":"))
        if isinstance(content, dict)
        else str(content)
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _artifact_version(obj: Any) -> str:
    """The artifact's own version: ``metadata.version`` (topologies, archetypes) or
    ``provenance.version`` (skills, funnels, contracts), '' if it declares neither."""
    raw = getattr(obj, "raw", None)
    meta = getattr(raw, "metadata", None)
    version = getattr(meta, "version", "") or ""
    if not version:
        version = getattr(getattr(raw, "provenance", None), "version", "") or ""
    return str(version)


def _artifact_entries(
    svc: Any, kind: str, ids_versions: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """``[{id, version, content_hash, content, yaml}]`` for one kind — content read from the
    workspace YAML via the ArtifactService. ``yaml`` is the file as written (comments, order,
    layout): a fleet that adopts the artifact and deploys it elsewhere writes that text back rather
    than a re-serialised dict, so a deploy does not strip the operator's annotations. Unreadable
    files are skipped, never fatal."""
    entries: list[dict[str, Any]] = []
    for aid, version in ids_versions:
        try:
            text = svc.read_yaml(kind, aid)
            content = yaml.safe_load(text) or {}
        except Exception:
            logger.debug("instance-state: could not read %s '%s'", kind, aid, exc_info=True)
            continue
        entries.append(
            {
                "id": aid,
                "version": version,
                "content_hash": _content_hash(content),
                "content": content,
                "yaml": text,
            }
        )
    return entries


def _role_registry_files(svc: Any) -> list[tuple[str, str]]:
    """``(id, version)`` for every ``roles/*.yaml`` — by ``metadata.id`` (or name), like the
    other kinds are keyed. Unreadable files are skipped."""
    out: list[tuple[str, str]] = []
    try:
        role_dir = svc._artifact_dir("role")
    except Exception:
        return out
    if not role_dir.is_dir():
        return out
    for f in sorted(role_dir.rglob("*.yaml")):
        try:
            raw = yaml.safe_load(f.read_text()) or {}
        except Exception:
            continue
        meta = raw.get("metadata", {}) if isinstance(raw, dict) else {}
        rid = str(meta.get("id") or meta.get("name") or "")
        if rid:
            out.append((rid, str(meta.get("version", "") or "")))
    return out


def _build_instance_state(rt: Any, svc: Any) -> dict[str, Any]:
    """The full observed state of this instance — every artifact's *content*, not just names.

    Unlike ``_build_capabilities`` (cheap, names-only, for liveness), this is what a fleet caches
    (offline-resilient) and can adopt into its registry. See
    design/details/control-plane/19-fleet-enrollment-protocol.md (Phase 1 — Observe).
    """
    ws = rt.workspace
    caps = _build_capabilities(rt)

    topos = [(k, _artifact_version(v)) for k, v in sorted(ws.topologies.items())]
    skills = [(k, _artifact_version(v)) for k, v in sorted(ws.skills.items())]
    archs = [(k, _artifact_version(v)) for k, v in sorted(ws.archetypes.items())]
    funnels = [(k, _artifact_version(v)) for k, v in sorted(ws.funnels.items())]
    contracts = [(k, _artifact_version(v)) for k, v in sorted(ws.contracts.items())]
    # Role registries resolve into one merged registry, so the files are enumerated from disk:
    # a fleet needs each registry as the artifact it is (design/details/control-plane/27).
    roles = _role_registry_files(svc)

    triggers: list[dict[str, Any]] = []
    for t in getattr(ws, "triggers", []) or []:
        try:
            raw = getattr(t, "raw", None)
            if raw is None:
                continue
            content = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else {}
            meta = getattr(raw, "metadata", None)
            tid = str(getattr(meta, "id", "") or getattr(meta, "name", ""))
            triggers.append(
                {
                    "id": tid,
                    "version": _artifact_version(t),
                    "content_hash": _content_hash(content),
                    "content": content,
                }
            )
        except Exception:
            logger.debug("instance-state: could not serialise a trigger", exc_info=True)

    return {
        "apiVersion": "swarmkit/v1",
        "kind": "InstanceState",
        "workspace_id": caps["workspace_id"],
        "workspace_name": caps["workspace_name"],
        "schema_version": caps["schema_version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "artifacts": {
            "topologies": _artifact_entries(svc, "topology", topos),
            "skills": _artifact_entries(svc, "skill", skills),
            "archetypes": _artifact_entries(svc, "archetype", archs),
            "triggers": triggers,
            "funnels": _artifact_entries(svc, "funnel", funnels),
            "contracts": _artifact_entries(svc, "contract", contracts),
            "roles": _artifact_entries(svc, "role", roles),
        },
        "providers": caps["model_providers"],
        "governance_provider": caps["governance_provider"],
        # A2A, both directions: whether this instance serves an agent card, and which remote
        # agents it calls (design/details/a2a-interop.md; a fleet's card listing, design 27).
        "a2a": {
            "enabled": bool(caps["features"].get("a2a")),
            "card_url": "/.well-known/agent-card.json" if caps["features"].get("a2a") else None,
            "remote_agents": remote_agent_rows(rt),
        },
        "health": {"status": "ok"},
    }


def _instance_state_manifest(state: dict[str, Any]) -> dict[str, Any]:
    """The names-only projection of an ``InstanceState`` — same shape, but each artifact entry keeps
    only ``id``/``version``/``content_hash`` (no ``content``). The cheap delta-sync primitive: a
    fleet pulls this, diffs the hashes against its cache, and fetches only the *changed* bodies
    (design 19 §delta sync). Metadata (workspace_id, schema_version, providers, …) is preserved."""
    manifest = {k: v for k, v in state.items() if k != "artifacts"}
    manifest["artifacts"] = {
        collection: [
            {ek: ev for ek, ev in entry.items() if ek not in ("content", "yaml")}
            for entry in entries
        ]
        for collection, entries in state.get("artifacts", {}).items()
    }
    return manifest


def _filter_instance_state(state: dict[str, Any], refs: list[tuple[str, str]]) -> dict[str, Any]:
    """Return an ``InstanceState`` carrying only the requested artifacts *with content*. ``refs`` is
    a list of ``(collection, id)`` pairs (collection = topologies/skills/archetypes/triggers). The
    manifest metadata is preserved; every collection is present but holds only the requested entries
    — this is the body-fetch half of delta sync."""
    wanted: dict[str, set[str]] = {}
    for collection, artifact_id in refs:
        wanted.setdefault(collection, set()).add(artifact_id)
    filtered = {k: v for k, v in state.items() if k != "artifacts"}
    filtered["artifacts"] = {
        collection: [e for e in entries if e.get("id") in wanted.get(collection, set())]
        for collection, entries in state.get("artifacts", {}).items()
    }
    return filtered


#: Fleet-read routes any valid membership (``monitor`` is the minimum) may authenticate to. The
#: delta-sync manifest is a read; the body-fetch POST (``/fleet/state/artifacts``) is handled
#: explicitly below (it is a read despite the POST verb — the body only carries the ref list).
_MEMBERSHIP_READ_ROUTES = frozenset({"/fleet/state", "/fleet/state/manifest"})
#: Deploy write routes — the ``PUT /api/{collection}/{id}`` targets. Only a ``manage`` membership
#: may authenticate to these (governed deploy over the membership credential, design 20). Derived
#: from the connector's deployable kinds so a kind added there is deployable here too — the two
#: lists drifted once (funnels: deployable by the panel, 401 at the instance).
_MEMBERSHIP_DEPLOY_PREFIXES = tuple(f"/api/{plural}/" for plural in DEPLOY_PLURAL.values())


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _require_signed_deploy() -> bool:
    """Whether a fleet deploy must carry a valid signature (design 22). An explicit
    ``SWARMKIT_FLEET_REQUIRE_SIGNED_DEPLOY`` wins; otherwise it **follows** ``require_identity`` —
    an instance that requires a fleet identity also requires signed deploys."""
    override = os.environ.get("SWARMKIT_FLEET_REQUIRE_SIGNED_DEPLOY", "").strip().lower()
    if override:
        return override in {"1", "true", "yes", "on"}
    return _env_truthy("SWARMKIT_FLEET_REQUIRE_IDENTITY")


def _verify_signed_deploy(
    request: Any,
    kind: str,
    artifact_id: str,
    content: Any,
    body_fleet_id: str | None,
    deploy_seq: int | None,
) -> None:
    """Verify the ``X-Fleet-Signature`` over a deploy's content against the pinned fleet key (design
    22). The fleet identity comes from the authenticated membership (Mode A) or an explicit
    ``fleet_id`` in the body (Mode B, the connector applying locally). Raises HTTPException(401) on
    an invalid signature — or on a **missing** one when signing is required. When a ``deploy_seq``
    is bound, it is part of the signed message *and* must be strictly newer than the last applied
    for this (fleet, kind, id) — else HTTPException(409), the downgrade guard. An operator/
    transport deploy with no fleet context passes through untouched (the operator is a trusted
    admin)."""
    membership = getattr(request.state, "membership", None)
    fleet_id = getattr(membership, "fleet_id", None) or body_fleet_id
    if fleet_id is None:
        return  # no fleet identity involved (operator transport-token deploy)
    signature = request.headers.get("X-Fleet-Signature", "")
    store = request.app.state.membership_store
    pinned = store.get_fleet_key(fleet_id)
    if signature and pinned is not None:
        # We hold this fleet's pinned key (Mode A register) → the signature must verify (over the
        # content hash and, when bound, the deploy sequence — so the seq can't be stripped/bumped).
        message = deploy_message(kind, artifact_id, _content_hash(content), deploy_seq)
        if not verify_signature(pinned, signature, message):
            raise HTTPException(401, "invalid fleet deploy signature")
        # Downgrade/replay guard: a bound sequence must advance past the last applied.
        if deploy_seq is not None and not store.deploy_seq_ok(
            fleet_id, kind, artifact_id, deploy_seq
        ):
            raise HTTPException(
                409, "stale deploy — sequence is not newer than the last applied (replay/downgrade)"
            )
    elif _require_signed_deploy() and not (signature and pinned is None):
        # Required but unverifiable: no signature at all. (A signature with no pinned key — a Mode B
        # instance whose serve never pinned the fleet, since the *panel* did at join — is accepted:
        # the call is already authenticated and there's nothing here to verify against.)
        raise HTTPException(
            401, "this instance requires a signed deploy (X-Fleet-Signature) from the fleet"
        )


def _asserted_actor(request: Any, item_id: str) -> tuple[str, str] | None:
    """The person a fleet asserts is resolving *item_id* through it, verified (design 28):
    ``(subject, fleet_id)``, or None when the request carries no assertion.

    The fleet must hold an ``approve-as`` membership here and a pinned identity; the signature must
    verify over ``actor_message(item_id, subject, issued_at)``; ``issued_at`` must be within the
    assertion TTL. Any failure is a 401 naming the reason — an assertion that does not verify must
    not fall back to the enrolment key, or the panel could never tell the two outcomes apart."""
    subject = request.headers.get("X-Fleet-Actor", "").strip()
    if not subject:
        return None
    fleet_id = request.headers.get("X-Fleet-Id", "").strip()
    signature = request.headers.get("X-Fleet-Actor-Signature", "")
    issued_raw = request.headers.get("X-Fleet-Actor-Issued", "")
    if not (fleet_id and signature and issued_raw):
        raise HTTPException(401, "an actor assertion needs X-Fleet-Id, -Signature and -Issued")
    store = request.app.state.membership_store
    membership = store.membership_for_fleet(fleet_id)
    if membership is None:
        raise HTTPException(401, f"fleet {fleet_id} holds no membership on this instance")
    if not scope_covers(str(membership.scope), "approve-as"):
        raise HTTPException(
            401,
            f"fleet {fleet_id} is not granted approve-as on this instance "
            f"(its scope is {membership.scope}); re-register with an approve-as enrollment token",
        )
    pinned = store.get_fleet_key(fleet_id)
    if pinned is None:
        raise HTTPException(401, f"fleet {fleet_id} has no pinned identity on this instance")
    try:
        issued_at = int(issued_raw)
    except ValueError:
        raise HTTPException(401, "X-Fleet-Actor-Issued must be unix seconds") from None
    if abs(int(time.time()) - issued_at) > ACTOR_ASSERTION_TTL_S:
        raise HTTPException(401, "actor assertion is stale — outside the assertion window")
    if not verify_signature(pinned, signature, actor_message(item_id, subject, issued_at)):
        raise HTTPException(401, "invalid fleet actor signature")
    return subject, fleet_id


def _membership_authenticates(request: Any, method: str, path: str) -> bool:  # noqa: PLR0911
    """True if *method*+*path* accepts membership auth (design 19/20) and the request carries a
    valid membership key as a Bearer token. This is the fallback the transport-auth seam consults
    when a caller presents a membership credential rather than a serve token. Scope-aware:

    * ``monitor`` (and ``manage``) → the fleet-read routes (a fleet reading its instance's state).
    * ``manage`` only → the deploy write routes (``PUT /api/{collection}/{id}``) — governed deploy
      over the membership credential (design 20). ``monitor`` is refused there.
    """
    store = getattr(request.app.state, "membership_store", None)
    if store is None:
        return False
    header = request.headers.get("Authorization", "")
    key = header[7:] if header.startswith("Bearer ") else ""
    membership = store.authenticate(key) if key else None
    if membership is None:
        return False
    # Stash the authenticated membership so the deploy handler can reach its fleet_id to verify a
    # signed push (design 22) — set only when a membership authenticates, not for transport tokens.
    request.state.membership = membership
    if path in _MEMBERSHIP_READ_ROUTES:
        return True  # any valid membership may read
    if method.upper() == "POST" and path == "/fleet/state/artifacts":
        return True  # delta-sync body fetch — a read (POST only for the ref list)
    if method.upper() == "PUT" and path.startswith(_MEMBERSHIP_DEPLOY_PREFIXES):
        # deploy needs manage (approve-as implies it)
        return scope_covers(str(getattr(membership, "scope", "")), "manage")
    if method.upper() == "DELETE" and path.startswith("/fleet/membership/"):
        # A fleet may revoke ONLY its own membership (self-leave) — "membership key or local admin"
        # (design 19). It can't eject another fleet; that stays a serve:admin owner action.
        return path == f"/fleet/membership/{membership.membership_id}"
    return False


def _record_serve_access(request: Any, identity: Any, action: str | None, status: int) -> None:
    """Append a serve access-audit record (best-effort; never breaks the request)."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        return
    try:
        store.record_access(
            client_id=identity.client_id,
            provider=identity.provider,
            method=request.method,
            path=request.url.path,
            action=action,
            status=status,
        )
    except Exception:  # audit must not break serving
        logger.debug("serve access-audit write failed", exc_info=True)


def _get_runtime(request: Request) -> WorkspaceRuntime:
    runtime: WorkspaceRuntime | None = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Workspace not loaded yet")
    return runtime


async def swap_runtime(app: Any, new_rt: Any) -> None:
    """Install a rebuilt runtime the way boot installed the first one — start its MCP servers
    when serve is configured to, make it current, then close the one it replaces.

    Every reload used to just assign ``app.state.runtime``: the old runtime's servers were never
    closed (one more set of subprocesses per reload) and the new one's were never started, so the
    first request to need a tool opened sessions in its own task — which the MCP transport then
    refused to close from anywhere else. Serialised, so two reloads cannot interleave.
    """
    if new_rt is None:
        return
    lock = getattr(app.state, "runtime_swap_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.runtime_swap_lock = lock
    async with lock:
        cfg = getattr(app.state, "server_config", None)
        if cfg is None or getattr(cfg, "mcp_enabled", True):
            try:
                await new_rt.start_session()
            except Exception:
                logger.warning(
                    "MCP server start failed after reload; runs will manage per-invocation",
                    exc_info=True,
                )
        old = getattr(app.state, "runtime", None)
        app.state.runtime = new_rt
        if old is not None and old is not new_rt:
            try:
                await old.close()
            except Exception:
                logger.warning("closing the replaced runtime raised", exc_info=True)
