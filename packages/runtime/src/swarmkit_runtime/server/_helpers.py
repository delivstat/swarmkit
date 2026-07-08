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
    # Listing / ejecting memberships is the instance owner's call (admin). /fleet/refresh is
    # exempt at the seam (it authenticates with the current membership key; see the middleware).
    if path.startswith("/fleet/membership"):
        return "admin"
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


#: Fleet-read routes any valid membership (``monitor`` is the minimum) may authenticate to.
_MEMBERSHIP_READ_ROUTES = frozenset({"/fleet/state"})
#: Deploy write routes — the ``PUT /api/{collection}/{id}`` targets. Only a ``manage`` membership
#: may authenticate to these (governed deploy over the membership credential, design 20).
_MEMBERSHIP_DEPLOY_PREFIXES = ("/api/topologies/", "/api/skills/", "/api/archetypes/")


def _membership_authenticates(request: Any, method: str, path: str) -> bool:
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
    if path in _MEMBERSHIP_READ_ROUTES:
        return True  # any valid membership may read
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
