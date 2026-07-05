"""Shared serve helpers — runtime accessor, route→scope mapping, capability advertisement,
access-audit, and webhook-signature validation. Split out so the route modules and the app
factory import them without an ``_app`` ⇄ routes cycle."""

from __future__ import annotations

import logging
import os
from typing import Any

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


def _required_action(method: str, path: str) -> str | None:
    """The serve:* tier action a route requires, or None for auth-exempt.

    read = all GETs (observe); admin = artifact mutation (/api/* writes) + canary
    promote/rollback; run = every other write (run/hooks/conversations/mcp). See
    design/details/control-plane/12-auth.md §4.
    """
    if path == "/health":
        return None
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
