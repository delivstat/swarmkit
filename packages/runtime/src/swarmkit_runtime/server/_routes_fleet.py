"""Fleet enrollment routes on serve — the instance side of the register handshake (design 19).

* ``POST /fleet/enroll-token`` (``serve:admin``) — the instance owner mints a one-time, TTL-bounded
  enrollment token (a join code). Admin-gated, so a ``manage``-scope join is human-issued.
* ``POST /fleet/register`` (**auth-exempt at the seam**; authenticates with the enrollment token) —
  consumes the token, creates a membership + issues the scoped API key the fleet will use to call
  this instance, and returns the full ``InstanceState`` in one round trip.
* ``POST /fleet/refresh`` (**auth-exempt**; authenticates with the *current* membership key) —
  rotates the key (old stops working); same membership.
* ``GET /fleet/memberships`` / ``DELETE /fleet/membership/{id}`` (``serve:admin``) — the owner lists
  the fleets registered here and ejects one (revoking its key). ``DELETE`` also accepts a membership
  key for the caller's **own** membership (self-leave) via the auth-seam fallback — a fleet may
  revoke itself but not another fleet (design 19).

``GET /fleet/state`` (in ``_routes_introspection``) now also accepts a membership key: when the
transport-auth seam rejects the bearer, a valid membership (monitor+ scope) authorizes the read
(the fallback lives in the serve auth middleware, ``_membership_authenticates``). This lets an
enrolled fleet read state with the credential it was issued, not a shared serve token. The
delta-sync pair — ``GET /fleet/state/manifest`` (names + hashes, no content) and
``POST /fleet/state/artifacts`` (fetch only the changed bodies) — share that same auth.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime.fleet import MembershipStore, fleet_id_from_public_key, verify_proof
from swarmkit_runtime.fleet._credentials import SCOPES, Scope

from ._helpers import _build_instance_state, _get_runtime
from ._services import ArtifactService

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class EnrollTokenRequest(BaseModel):
    scope: str = "monitor"  # monitor | manage
    ttl_seconds: int | None = None


class RegisterRequest(BaseModel):
    fleet_id: str
    requested_scope: str | None = None  # advisory; the token's scope is authoritative
    # Fleet identity (design 21). When present, fleet_id must equal fingerprint(fleet_public_key),
    # `proof` must be an Ed25519 signature over `<enrollment_token>:<target_workspace_id>`.
    fleet_public_key: str | None = None  # base64 Ed25519 public key
    proof: str | None = None  # base64 signature
    target_workspace_id: str | None = None  # the workspace the panel believes it's enrolling
    display_name: str | None = None  # cosmetic human label for the fleet


def _store(request: Request) -> MembershipStore:
    store: MembershipStore = request.app.state.membership_store
    return store


def _require_identity() -> bool:
    """Whether this instance requires a fleet identity off-loopback (design 21; opt-in this rel).
    Env toggle so it's a deployment/security decision, not workspace content."""
    return os.environ.get("SWARMKIT_FLEET_REQUIRE_IDENTITY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_loopback(request: Request) -> bool:
    return bool(request.client and request.client.host in _LOOPBACK_HOSTS)


def _verify_fleet_identity(
    req: RegisterRequest, enroll_token: str, workspace_id: str, request: Request
) -> None:
    """Validate a presented fleet identity (design 21): fleet_id self-certifies from the public key,
    the proof signs ``<token>:<target_workspace_id>``, and the claimed workspace matches this
    instance. No state change (pinning happens after the token is consumed). Raises HTTPException on
    any failure; a no-key register is allowed unless identity is required off-loopback."""
    if not req.fleet_public_key:
        if _require_identity() and not _is_loopback(request):
            raise HTTPException(
                401, "this instance requires a fleet identity (public key + proof) to register"
            )
        return  # opportunistic: no identity presented, none required
    if req.fleet_id != fleet_id_from_public_key(req.fleet_public_key):
        raise HTTPException(400, "fleet_id does not match the public key (not self-certifying)")
    claimed_ws = req.target_workspace_id or ""
    if not verify_proof(req.fleet_public_key, req.proof or "", enroll_token, claimed_ws):
        raise HTTPException(401, "invalid fleet identity proof")
    # Anti-replay: a proof bound to another instance's workspace must not be accepted here.
    if claimed_ws and claimed_ws != workspace_id:
        raise HTTPException(401, "fleet identity proof is bound to a different instance")


def _register_fleet_routes(app: FastAPI) -> None:
    @app.post("/fleet/enroll-token")
    async def mint_enroll_token(req: EnrollTokenRequest, request: Request) -> dict[str, Any]:
        if req.scope not in SCOPES:
            raise HTTPException(400, f"scope must be one of {SCOPES}")
        scope: Scope = req.scope
        kwargs = {"ttl_seconds": req.ttl_seconds} if req.ttl_seconds is not None else {}
        token = _store(request).create_enrollment_token(scope, **kwargs)
        return {"token": token, "scope": scope}  # shown once

    @app.post("/fleet/register")
    async def register(req: RegisterRequest, request: Request) -> dict[str, Any]:
        header = request.headers.get("Authorization", "")
        enroll_token = header[7:] if header.startswith("Bearer ") else ""
        if not enroll_token:
            raise HTTPException(401, "register requires a Bearer enrollment token")

        workspace_id = str(_get_runtime(request).workspace.raw.metadata.id)
        # Verify the fleet identity BEFORE consuming the token, so a signing bug doesn't burn a
        # good enrollment token (only a genuine key mismatch, checked after consume, does).
        _verify_fleet_identity(req, enroll_token, workspace_id, request)

        scope = _store(request).consume_enrollment_token(enroll_token)
        if scope is None:
            raise HTTPException(401, "invalid, expired, or already-used enrollment token")

        # Trust-on-first-use pin (after the token is validated) — a same-fleet_id/different-key
        # register is the SSH known_hosts moment.
        if req.fleet_public_key:
            outcome = _store(request).pin_fleet_key(
                req.fleet_id, req.fleet_public_key, req.display_name or ""
            )
            if outcome == "mismatch":
                raise HTTPException(
                    409, "fleet identity changed — pinned key mismatch (unpin to re-key)"
                )

        membership, key = _store(request).issue_membership(req.fleet_id, scope)
        svc = ArtifactService(request.app.state.workspace_path)
        state = _build_instance_state(_get_runtime(request), svc)
        return {
            "membership_id": membership.membership_id,
            "credential": {
                "type": "api_key",
                "value": key,  # shown once — only its hash is stored on this instance
                "scope": scope,
                "fingerprint": membership.key_fingerprint,
            },
            "instance_state": state,
        }

    @app.post("/fleet/refresh")
    async def refresh(request: Request) -> dict[str, Any]:
        """Rotate the caller's membership key. Authenticates with the *current* key (Bearer); the
        old key stops working. Same membership, new secret (shown once)."""
        header = request.headers.get("Authorization", "")
        current = header[7:] if header.startswith("Bearer ") else ""
        if not current:
            raise HTTPException(
                401, "refresh requires the current membership key as a Bearer token"
            )
        rotated = _store(request).rotate(current)
        if rotated is None:
            raise HTTPException(401, "invalid or expired membership key")
        membership, new_key = rotated
        return {
            "membership_id": membership.membership_id,
            "credential": {
                "type": "api_key",
                "value": new_key,
                "scope": membership.scope,
                "fingerprint": membership.key_fingerprint,
            },
        }

    @app.get("/fleet/memberships")
    async def list_memberships(request: Request) -> list[dict[str, Any]]:
        """The fleets registered with this instance (serve:admin — owner-only). No secrets; adds
        the pinned fleet public key (non-secret) so the owner sees each fleet's identity."""
        store = _store(request)
        return [
            {
                "membership_id": m.membership_id,
                "fleet_id": m.fleet_id,
                "scope": m.scope,
                "fingerprint": m.key_fingerprint,
                "created_at": m.created_at,
                "fleet_public_key": store.get_fleet_key(m.fleet_id),  # pinned key or None
            }
            for m in store.list_memberships()
        ]

    @app.delete("/fleet/identity/{fleet_id}")
    async def unpin_identity(fleet_id: str, request: Request) -> dict[str, Any]:
        """Forget a fleet's pinned public key (serve:admin) so it may deliberately re-key on the
        next register (design 21). Does not revoke memberships — use DELETE /fleet/membership."""
        if not _store(request).unpin_fleet_key(fleet_id):
            raise HTTPException(404, "no pinned identity for that fleet")
        return {"unpinned": fleet_id}

    @app.delete("/fleet/membership/{membership_id}")
    async def eject(membership_id: str, request: Request) -> dict[str, Any]:
        """Eject a fleet — revoke its membership; its key stops authenticating (serve:admin)."""
        if not _store(request).revoke_membership(membership_id):
            raise HTTPException(404, "membership not found")
        return {"ejected": membership_id}
