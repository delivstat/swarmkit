"""Request bodies for the panel API — the pydantic models FastAPI validates. Extracted from
``_app.py`` so the route modules share one definition and the app factory stays wiring-only."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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


class RegisterInstanceRequest(BaseModel):
    enroll_token: str  # the one-time join code the instance owner minted
    fleet_id: str | None = None  # defaults to the panel's fleet id
    requested_scope: str | None = None


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


class AuthorRequest(BaseModel):
    message: str
    topology: str = "authoring"


class GapProposeRequest(BaseModel):
    instance_id: str  # which instance's authoring swarm drafts the fix (Mode A)
    capability: str  # the gap to close (from GET /gaps)
    description: str = ""
    topology: str = "authoring"
    eval_topology: str = "eval"
