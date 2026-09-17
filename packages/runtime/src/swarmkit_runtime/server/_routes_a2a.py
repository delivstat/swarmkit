"""A2A routes — the Agent Card at the well-known path, the JSON-RPC task endpoint, and the
portal's two reads for *calling* other agents (probe a card; list the remote agents declared).

Thin: every method is answered by :class:`~swarmkit_runtime.server._a2a.A2AHandler`, which maps
it onto the same job service ``POST /run`` uses (design/details/a2a-interop.md). This module
owns only what is HTTP-shaped — where the card lives, how the base URL is derived, when a POST
streams — and the ``server.a2a.enabled`` switch, checked per request so a reload flips it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from swarmkit_runtime.agent_skill._remote import A2AClient, RemoteAgentError
from swarmkit_runtime.agent_skill._spec import parse_agent_spec
from swarmkit_runtime.auth import AuthProvider
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.skills import impl_get

from ._a2a import (
    INVALID_REQUEST,
    PARSE_ERROR,
    WELL_KNOWN_PATH,
    A2AHandler,
    build_agent_card,
    rpc_error,
)
from ._config import ServerCfg
from ._helpers import _get_runtime
from ._services import JobService

logger = logging.getLogger("swarmkit.server.a2a")

A2A_WELL_KNOWN_PATH = WELL_KNOWN_PATH

_STREAMING_METHODS = frozenset({"message/stream", "tasks/subscribe", "tasks/resubscribe"})


def _server_cfg(request: Request) -> ServerCfg:
    cfg: ServerCfg = getattr(request.app.state, "server_config", None) or ServerCfg()
    return cfg


def _require_enabled(request: Request) -> ServerCfg:
    cfg = _server_cfg(request)
    if not cfg.a2a_enabled:
        raise HTTPException(
            status_code=404,
            detail="A2A is not enabled on this workspace; set server.a2a.enabled: true",
        )
    return cfg


def _base_url(request: Request) -> str:
    """The address the card tells other agents to call — the proxy's, when one forwarded us."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        host = request.url.netloc
    return f"{proto}://{host}"


def _gate_url_for(workspace_path: Path, base_url: str) -> Any:
    """A function job → the URL of the gate it is parked on, or None when no item names it.

    Role-tasks carry `run_id` (`gate_id` is `{run_id}:{agent}`); a harness input request or a
    relay approval does not, so those fall back to the review item itself. Either way the caller
    is told *where* a person resolves it, never handed a way to resolve it.
    """

    def lookup(job: Any) -> str | None:
        try:
            pending = FileReviewQueue(workspace_path).list_pending()
        except OSError:
            return None
        job_id = str(getattr(job, "id", ""))
        for item in pending:
            out = item.output or {}
            gate_id = str(out.get("gate_id") or "")
            run_id = str(out.get("run_id") or gate_id.rpartition(":")[0])
            if run_id and run_id == job_id:
                return f"{base_url}/gates/{gate_id}" if gate_id else f"{base_url}/review/{item.id}"
        for item in pending:
            if job_id and job_id in item.id:
                return f"{base_url}/review/{item.id}"
        return None

    return lookup


def _register_a2a_routes(app: FastAPI, auth: AuthProvider, workspace_path: Path) -> None:
    """GET /.well-known/agent-card.json, GET /a2a[/{topology}]/card, POST /a2a[/{topology}]."""
    _register_a2a_client_routes(app)

    job_service = JobService(app.state.job_store)

    def _card(request: Request, only_topology: str | None) -> dict[str, Any]:
        cfg = _require_enabled(request)
        rt = _get_runtime(request)
        if only_topology is not None and only_topology not in rt.workspace.topologies:
            raise HTTPException(status_code=404, detail=f"Unknown topology '{only_topology}'")
        return build_agent_card(
            rt,
            base_url=_base_url(request),
            auth_provider=auth.mode,
            identity=cfg.a2a_identity,
            only_topology=only_topology,
        )

    @app.get(WELL_KNOWN_PATH)
    async def agent_card(request: Request) -> dict[str, Any]:
        """The instance's Agent Card — public, one skill per topology."""
        return _card(request, None)

    @app.get("/a2a/{topology}/card")
    async def topology_card(topology: str, request: Request) -> dict[str, Any]:
        """The per-topology card, for a client that should see one skill only."""
        return _card(request, topology)

    def _handler(request: Request, topology: str | None) -> A2AHandler:
        cfg = _require_enabled(request)
        rt = _get_runtime(request)
        if topology is not None and topology not in rt.workspace.topologies:
            raise HTTPException(status_code=404, detail=f"Unknown topology '{topology}'")
        identity = getattr(request.state, "identity", None)
        actor = str(getattr(identity, "client_id", "") or "a2a")
        state = request.app.state
        return A2AHandler(
            rt=rt,
            jobs=job_service,
            job_store=state.job_store,
            store=getattr(state, "store", None),
            cfg=cfg,
            canary=getattr(state, "canary_router", None),
            semaphore=getattr(state, "job_semaphore", None),
            gate_url_for=_gate_url_for(workspace_path, _base_url(request)),
            topology=topology,
            actor=actor,
        )

    async def _rpc(request: Request, topology: str | None) -> Response:
        handler = _handler(request, topology)
        try:
            body = json.loads(await request.body() or b"")
        except (ValueError, UnicodeDecodeError):
            return JSONResponse(rpc_error(None, PARSE_ERROR, "body is not JSON"))
        method = body.get("method") if isinstance(body, dict) else None
        wants_stream = "text/event-stream" in (request.headers.get("accept") or "")
        if method in _STREAMING_METHODS:
            if not wants_stream:
                return JSONResponse(
                    rpc_error(
                        body.get("id"),
                        INVALID_REQUEST,
                        f"{method} streams; send Accept: text/event-stream",
                    )
                )
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            return StreamingResponse(
                handler.stream(params, subscribe=method != "message/stream"),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        return JSONResponse(await handler.handle(body))

    @app.post("/a2a", response_model=None)
    async def a2a_rpc(request: Request) -> Response:
        """The JSON-RPC endpoint for every topology; the message names its skill."""
        return await _rpc(request, None)

    @app.post("/a2a/{topology}", response_model=None)
    async def a2a_topology_rpc(topology: str, request: Request) -> Response:
        """The per-topology JSON-RPC endpoint — the card's per-skill `url`."""
        return await _rpc(request, topology)


def _register_a2a_client_routes(app: FastAPI) -> None:
    """The portal's reads for *calling* other agents (a2a-interop.md "Discovery" 2)."""

    @app.get("/api/a2a/probe")
    async def probe_card(card_url: str) -> dict[str, Any]:
        """Fetch a remote Agent Card so a person can pick a skill *before* a skill file exists.

        Served by the runtime rather than fetched by the browser because a card elsewhere is
        cross-origin to the portal. Independent of `server.a2a.enabled`: calling out is not
        serving. Nothing is written; adding the agent is the person's next click.
        """
        try:
            card = await A2AClient(timeout_s=20.0).fetch_card(card_url)
        except RemoteAgentError as exc:
            return {"supported": False, "detail": str(exc)}
        raw = card.raw
        return {
            "supported": True,
            "name": card.name,
            "description": str(raw.get("description") or ""),
            "url": card.url,
            "streaming": card.streaming,
            "requires_bearer": bool(raw.get("securitySchemes")),
            "skills": [
                {
                    "id": str(s.get("id")),
                    "name": str(s.get("name") or s.get("id")),
                    "description": str(s.get("description") or ""),
                }
                for s in raw.get("skills") or []
                if isinstance(s, dict) and s.get("id")
            ],
        }

    @app.get("/api/a2a/agents")
    async def remote_agents(request: Request) -> list[dict[str, Any]]:
        """The remote agents this workspace can call — every `agent` skill with a `card_url`."""
        rt = _get_runtime(request)
        rows: list[dict[str, Any]] = []
        for sid, skill in sorted(rt.workspace.skills.items()):
            impl = skill.raw.implementation
            if impl_get(impl, "type") != "agent":
                continue
            try:
                spec = parse_agent_spec(impl)
            except ValueError:
                continue
            if spec.is_local:
                continue
            rows.append(
                {
                    "id": sid,
                    "name": str(getattr(skill.raw.metadata, "name", "") or sid),
                    "card_url": spec.card_url,
                    "skill_id": spec.skill_id,
                    "credentials_ref": spec.credentials_ref,
                    "on_unanswerable": spec.on_unanswerable,
                    "permission": spec.permission,
                    "effects": spec.effects,
                    "timeout_s": spec.timeout_s,
                }
            )
        return rows
