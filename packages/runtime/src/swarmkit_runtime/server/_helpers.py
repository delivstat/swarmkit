"""Shared serve helpers — runtime accessor, route→scope mapping, capability advertisement,
access-audit, and webhook-signature validation. Split out so the route modules and the app
factory import them without an ``_app`` ⇄ routes cycle."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import yaml
from fastapi import HTTPException, Request

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.fleet import deploy_message, verify_signature
from swarmkit_runtime.triggers._webhook import validate_webhook_signature

logger = logging.getLogger("swarmkit.server")


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
        secret = os.environ.get(secret_ref, "")
        if not secret:
            logger.warning(
                "Webhook trigger secret_ref=%r not found in environment; "
                "skipping signature validation for topology=%r",
                secret_ref,
                topology_name,
            )
            continue
        header_name = auth.get("header", "X-Hub-Signature-256")
        sig = request.headers.get(header_name, "")
        if not validate_webhook_signature(raw_body, sig, secret):
            logger.warning(
                "Webhook signature validation failed for topology=%r",
                topology_name,
            )
            raise HTTPException(
                status_code=401,
                detail="Invalid webhook signature",
            )
        break


def _required_action(method: str, path: str) -> str | None:  # noqa: PLR0911
    """The serve:* tier action a route requires, or None for auth-exempt.

    read = all GETs (observe); admin = artifact mutation (/api/* writes) + canary
    promote/rollback; run = every other write (run/hooks/conversations/mcp). See
    design/details/control-plane/12-auth.md §4.
    """
    if path == "/health":
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
    if method.upper() == "GET":
        return "read"
    if path.startswith("/api/"):
        return "admin"
    if path.startswith("/canary/") and (path.endswith("/promote") or path.endswith("/rollback")):
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
        "topologies": sorted(ws.topologies.keys()),
        "model_providers": rt.provider_registry.provider_ids,
        "governance_provider": _enum_value(getattr(raw, "governance", None), "provider", "mock"),
        "features": {
            "auth": _enum_value(getattr(server_raw, "auth", None), "provider", "none"),
            "compression": _enum_value(getattr(raw, "context_compression", None), "backend", "off"),
            "canary": bool(getattr(canary, "routes", None)),
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
    """``metadata.version`` off a resolved artifact, or '' if absent."""
    meta = getattr(getattr(obj, "raw", None), "metadata", None)
    return str(getattr(meta, "version", "") or "")


def _artifact_entries(
    svc: Any, kind: str, ids_versions: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """``[{id, version, content_hash, content}]`` for one kind — content read from the workspace
    YAML via the ArtifactService. Unreadable files are skipped, never fatal."""
    entries: list[dict[str, Any]] = []
    for aid, version in ids_versions:
        try:
            content = yaml.safe_load(svc.read_yaml(kind, aid)) or {}
        except Exception:
            logger.debug("instance-state: could not read %s '%s'", kind, aid, exc_info=True)
            continue
        entries.append(
            {
                "id": aid,
                "version": version,
                "content_hash": _content_hash(content),
                "content": content,
            }
        )
    return entries


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
        },
        "providers": caps["model_providers"],
        "governance_provider": caps["governance_provider"],
        "health": {"status": "ok"},
    }


def _instance_state_manifest(state: dict[str, Any]) -> dict[str, Any]:
    """The names-only projection of an ``InstanceState`` — same shape, but each artifact entry keeps
    only ``id``/``version``/``content_hash`` (no ``content``). The cheap delta-sync primitive: a
    fleet pulls this, diffs the hashes against its cache, and fetches only the *changed* bodies
    (design 19 §delta sync). Metadata (workspace_id, schema_version, providers, …) is preserved."""
    manifest = {k: v for k, v in state.items() if k != "artifacts"}
    manifest["artifacts"] = {
        collection: [{ek: ev for ek, ev in entry.items() if ek != "content"} for entry in entries]
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
#: may authenticate to these (governed deploy over the membership credential, design 20).
_MEMBERSHIP_DEPLOY_PREFIXES = ("/api/topologies/", "/api/skills/", "/api/archetypes/")


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
        return getattr(membership, "scope", None) == "manage"  # deploy is manage-only
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
