"""The FastAPI app factory — lifespan (resolve workspace, boot MCP + scheduler + canary),
CORS, request-log + auth middleware, and the route wiring. Everything else in this package
feeds ``create_app``."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from swarmkit_runtime._workspace_runtime import MissingMCPServerError, WorkspaceRuntime
from swarmkit_runtime.auth import AuthError, AuthProvider, NoneAuthProvider
from swarmkit_runtime.auth import AuthRequest as AuthReq
from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.persistence import create_store

from ._config import (
    _parse_canary_routes,
    _parse_server_config,
    _parse_trigger_configs,
)
from ._helpers import _record_serve_access, _required_action
from ._jobs import JobStore
from ._mcp import _boot_mcp, _mcp_available, _mount_mcp, _start_scheduler
from ._routes_conversations import _register_conversation_routes
from ._routes_crud import _register_crud_routes
from ._routes_introspection import _register_introspection_routes
from ._routes_jobs import _register_job_routes
from ._services import ArtifactService

logger = logging.getLogger("swarmkit.server")


def create_app(  # noqa: PLR0915
    workspace_path: Path,
    *,
    cors_origins: list[str] | None = None,
    auth_provider: AuthProvider | None = None,
    host: str = "127.0.0.1",
    insecure: bool = False,
) -> FastAPI:
    """Build the FastAPI app for a given workspace."""

    _auth = auth_provider or NoneAuthProvider()
    # Default-secure lives here (not just in the CLI) so every embedder inherits it: an
    # unauthenticated serve on a non-loopback bind refuses to start unless opted in.
    _loopback = {"127.0.0.1", "::1", "localhost", ""}
    if isinstance(_auth, NoneAuthProvider) and not insecure and host not in _loopback:
        raise RuntimeError(
            f"refusing to serve with auth provider 'none' on a non-loopback bind ({host!r}). "
            "Configure server.auth (api_key/jwt), bind 127.0.0.1, or pass insecure=True."
        )
    job_store = JobStore()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        try:
            runtime = WorkspaceRuntime.from_workspace_path(workspace_path)
        except ResolutionErrors as exc:
            errors = [{"code": e.code, "message": e.message} for e in exc.errors]
            raise RuntimeError(f"Workspace failed to resolve: {errors}") from exc
        except MissingMCPServerError as exc:
            raise RuntimeError(str(exc)) from exc

        app.state.runtime = runtime
        app.state.store = create_store(workspace_path, runtime.workspace.raw)
        app.state.workspace_path = workspace_path  # for GET /fleet/state (reads artifact content)

        # Parse server config from workspace.yaml
        cfg = _parse_server_config(runtime.workspace)
        app.state.server_config = cfg
        app.state.job_semaphore = asyncio.Semaphore(cfg.max_concurrent)
        logger.info(
            "Server config: max_concurrent=%d, timeout=%ds, mcp_enabled=%s",
            cfg.max_concurrent,
            cfg.timeout_seconds,
            cfg.mcp_enabled,
        )

        await _boot_mcp(runtime, cfg)

        # Build trigger configs and start the cron scheduler
        trigger_configs = _parse_trigger_configs(runtime.workspace)
        app.state.trigger_configs = trigger_configs
        scheduler = await _start_scheduler(app, job_store, trigger_configs)
        app.state.scheduler = scheduler

        # Initialize canary router if configured
        canary_routes = _parse_canary_routes(runtime.workspace)
        if canary_routes:
            available: dict[str, set[str]] = {
                name.split("@")[0]: set() for name in runtime.workspace.topologies
            }
            for name in runtime.workspace.topologies:
                base = name.split("@")[0]
                topo = runtime.workspace.topologies[name]
                available[base].add(topo.raw.metadata.version)
            app.state.canary_router = CanaryRouter(canary_routes, available)
        else:
            app.state.canary_router = None

        yield
        await scheduler.stop()
        await runtime.close()

    app = FastAPI(
        title="SwarmKit",
        description="HTTP interface over a SwarmKit workspace.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS is added only when origins are explicitly configured — no wildcard default, and
    # never "*" with credentials (which the browser rejects and which opens the API to any
    # site). Without configured origins the API is same-origin only.
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start
        logger.info(
            "%s %s -> %s (%.3fs)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        # Skip auth for health endpoint
        if request.url.path == "/health":
            return await call_next(request)

        auth_req = AuthReq(
            headers=dict(request.headers),
            path=request.url.path,
            method=request.method,
            query_params=dict(request.query_params),
            client_ip=request.client.host if request.client else None,
        )
        try:
            identity = await _auth.authenticate(auth_req)
            request.state.identity = identity
            logger.debug(
                "auth.success client_id=%s provider=%s",
                identity.client_id,
                identity.provider,
            )
        except AuthError as exc:
            logger.warning(
                "auth.denied path=%s reason=%s",
                request.url.path,
                str(exc),
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": str(exc)},
            )

        # Per-route authorization: each route requires a serve:* tier scope.
        required = _required_action(request.method, request.url.path)
        if required is not None and not await _auth.authorize(identity, "serve", required):
            logger.warning(
                "authz.denied client_id=%s path=%s required=serve:%s",
                identity.client_id,
                request.url.path,
                required,
            )
            _record_serve_access(request, identity, required, 403)
            return JSONResponse(
                status_code=403,
                content={"error": f"Insufficient scope: requires serve:{required}"},
            )

        response = await call_next(request)
        # Audit mutating calls (run/admin) with the acting client_id.
        if required in ("run", "admin"):
            _record_serve_access(request, identity, required, response.status_code)
        return response

    # Routes
    _register_introspection_routes(app)
    _register_job_routes(app, job_store)
    _register_conversation_routes(app, workspace_path)
    _register_crud_routes(app, ArtifactService(workspace_path))

    if _mcp_available:
        _mount_mcp(app)
    else:
        logger.warning("mcp package not installed; /mcp endpoint disabled")

    return app
