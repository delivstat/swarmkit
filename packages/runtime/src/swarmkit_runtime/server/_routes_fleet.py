"""Fleet enrollment routes on serve — the instance side of the register handshake (design 19).

Two routes:

* ``POST /fleet/enroll-token`` (``serve:admin``) — the instance owner mints a one-time, TTL-bounded
  enrollment token (a join code). Admin-gated, so a ``manage``-scope join is human-issued.
* ``POST /fleet/register`` (**auth-exempt at the seam**; authenticates with the enrollment token in
  its ``Authorization`` header) — consumes the token, creates a membership + issues the scoped API
  key the fleet will use to call this instance, and returns the full ``InstanceState`` in one round
  trip.

Phase 1 (``GET /fleet/state``) is unchanged; gating it behind the membership key is a later slice.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime.fleet import MembershipStore
from swarmkit_runtime.fleet._credentials import SCOPES, Scope

from ._helpers import _build_instance_state, _get_runtime
from ._services import ArtifactService


class EnrollTokenRequest(BaseModel):
    scope: str = "monitor"  # monitor | manage
    ttl_seconds: int | None = None


class RegisterRequest(BaseModel):
    fleet_id: str
    requested_scope: str | None = None  # advisory; the token's scope is authoritative


def _store(request: Request) -> MembershipStore:
    store: MembershipStore = request.app.state.membership_store
    return store


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
        scope = _store(request).consume_enrollment_token(enroll_token)
        if scope is None:
            raise HTTPException(401, "invalid, expired, or already-used enrollment token")

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
