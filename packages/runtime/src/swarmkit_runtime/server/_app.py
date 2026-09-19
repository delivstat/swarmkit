"""The FastAPI app factory — lifespan (resolve workspace, boot MCP + scheduler + canary),
CORS, request-log + auth middleware, and the route wiring. Everything else in this package
feeds ``create_app``."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from swarmkit_runtime._workspace_runtime import MissingMCPServerError, WorkspaceRuntime
from swarmkit_runtime.auth import AuthError, AuthProvider, NoneAuthProvider
from swarmkit_runtime.auth import AuthRequest as AuthReq
from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.oauth import PendingLogins, TokenStore
from swarmkit_runtime.persistence import storage_for_workspace
from swarmkit_runtime.telemetry import configure_telemetry, load_telemetry_config

from ._config import (
    _parse_canary_routes,
    _parse_server_config,
    _parse_trigger_configs,
)
from ._helpers import _membership_authenticates, _record_serve_access, _required_action
from ._jobs import JobStore
from ._mcp import _boot_mcp, _mcp_available, _mount_mcp, _start_scheduler, mcp_session_lifespan
from ._routes_a2a import A2A_WELL_KNOWN_PATH, _register_a2a_routes
from ._routes_config import _register_config_routes
from ._routes_conversations import _register_conversation_routes
from ._routes_crud import _register_crud_routes
from ._routes_events import _register_event_routes
from ._routes_events_stream import _register_event_stream_routes
from ._routes_fleet import _register_fleet_routes
from ._routes_introspection import _register_introspection_routes
from ._routes_jobs import _register_job_routes
from ._routes_memory import _register_memory_routes
from ._routes_oauth import OAuthService, _register_oauth_routes
from ._routes_review import _register_review_routes
from ._routes_skill_registry import _register_skill_registry_routes
from ._services import ArtifactService
from ._webui import mount_webui
from ._workspace_config import WorkspaceConfigService

logger = logging.getLogger("swarmkit.server")


def _webhook_has_its_own_auth(app: FastAPI, name: str) -> bool:
    """True when a webhook trigger addressed by *name* (a target topology, or a pipeline trigger's
    own id) declares `config.auth` — the signature the route will verify. A webhook trigger with
    no auth block stays behind the serve gate: turning `server.auth` on must not open an unsigned
    door."""
    for tc in getattr(app.state, "trigger_configs", None) or []:
        if tc.get("type") != "webhook":
            continue
        if not ((tc.get("config") or {}).get("auth") or (tc.get("config") or {}).get("secret_ref")):
            continue
        if name in (tc.get("targets") or []) or tc.get("id") == name:
            return True
    return False


def _wire_storage(app: FastAPI, workspace_path: Path, runtime: WorkspaceRuntime) -> Any:
    """Resolve every store ONCE, through the one service, and report what it chose.

    Serve used to build four stores from three different resolvers; the pipeline CLI and the
    orchestrator built more. They agreed only while all of them ignored configuration and landed
    on the same SQLite file (design/details/storage-service.md). The
    caller needs for the signal sink and the run-stage seam.
    """
    storage = storage_for_workspace(workspace_path, runtime.workspace.raw)
    storage.log_report()
    app.state.storage = storage
    app.state.store = storage.store()
    # Fleet enrollment store, on the same backend as the main store (design 19 Q4).
    app.state.membership_store = storage.membership_store()
    app.state.artifact_store = storage.artifact_store()


def _log_unreachable(runtime: Any) -> None:
    """Report declared configuration that no code path reaches, once, at startup.

    Best-effort: a check that can crash the server is worse than a check that is absent, and this
    one compiles every topology to answer.
    """
    try:
        report = runtime.reachability()
    except Exception:
        logger.debug("reachability check skipped", exc_info=True)
        return
    if report.ok:
        return
    for item in report.unreachable:
        logger.warning("declared but unreachable: %s", item.line())


def _sweep_stale_jobs(store: Any, timeout_seconds: int) -> None:
    """Mark jobs a dead process left `running` as interrupted, and say how many.

    Best-effort: a store that cannot be swept is a reason to lose the cleanup, never a reason to
    refuse to start serving.
    """
    if store is None:
        return
    try:
        swept = store.sweep_stale_jobs(
            timeout_seconds, "the server restarted while this run was in flight"
        )
    except Exception:
        logger.warning("could not close jobs left running by a previous process")
        return
    if swept:
        logger.info("Marked %d job(s) interrupted: left running by a previous process", swept)


def _warn_if_identity_is_not_a_role_member(auth: AuthProvider, runtime: Any) -> None:
    """Warn when the asserted identity matches no member in the role registry.

    A gate is resolved by matching the caller's ``client_id`` against ``members:`` in
    ``swarm/roles.yaml``. When it matches nothing, every approval fails with a 403 that looks
    exactly like being unauthenticated — so the operator debugs their token or their login when the
    real problem is a name.

    Only providers with a *static* asserted identity can be checked here, which today means
    ``none``. The same trap exists under ``jwt`` — most identity providers put a UUID in ``sub`` by
    default, which authenticates fine and then fails every role check — but that identity arrives
    per request, so catching it needs a check at resolve time rather than at startup.

    A warning, never a refusal: a workspace with no gates is a legitimate configuration, and so is
    one whose approvers are named elsewhere.
    """
    identity = getattr(auth, "identity", None)
    client_id = getattr(identity, "client_id", "") if identity is not None else ""
    if not client_id:
        return
    registry = getattr(getattr(runtime, "workspace", None), "role_registry", None)
    roles = dict(getattr(registry, "roles", None) or {})
    if not roles:
        return  # no roles declared: nothing to be inconsistent with
    if any(client_id in role.members for role in roles.values()):
        return
    logger.warning(
        "auth identity %r is not a member of any role in swarm/roles.yaml — approval gates will "
        "reject it with a 403 that reads like an authentication failure. Add it under `members:` "
        "for the role that should approve, or set server.auth.config.identity to a listed member.",
        client_id,
    )


def create_app(  # noqa: PLR0915
    workspace_path: Path,
    *,
    cors_origins: list[str] | None = None,
    auth_provider: AuthProvider | None = None,
    host: str = "127.0.0.1",
    insecure: bool = False,
    enqueue_only: bool = False,
    queue_max_depth: int = 0,
    profile: str = "standard",
) -> FastAPI:
    """Build the FastAPI app for a given workspace.

    *enqueue_only* is the API tier of the API/worker split (``serve --role api``,
    worker-execution.md): ``POST /run`` persists the job as ``queued`` and returns, and a
    ``swarmkit worker`` process claims and executes it. Default False is the all-in-one server.

    *queue_max_depth* bounds the queued backlog in that mode — a submit is refused with 429 once
    this many runs are waiting, so the queue cannot grow without limit. 0 means unbounded; the CLI
    sets a non-zero default.

    *profile* ``"production"`` runs a fail-closed preflight (production-profile.md) and refuses to
    start unless the deployment is safe to expose — real auth, a persistent ``SWARMKIT_OAUTH_KEY``,
    sandboxed MCP, no wildcard CORS, not ``--insecure``. ``"standard"`` (default) is unchanged.
    """

    _auth = auth_provider or NoneAuthProvider()
    # Default-secure lives here (not just in the CLI) so every embedder inherits it: an
    # unauthenticated serve on a non-loopback bind refuses to start unless opted in.
    _loopback = {"127.0.0.1", "::1", "localhost", ""}
    if isinstance(_auth, NoneAuthProvider) and not insecure and host not in _loopback:
        raise RuntimeError(
            f"refusing to serve with auth provider 'none' on a non-loopback bind ({host!r}). "
            "Configure server.auth (api_key/jwt), bind 127.0.0.1, or pass insecure=True."
        )
    # Fail-closed profile: refuse to start unless the deployment is production-safe, listing every
    # gap at once (production-profile.md). Runs in addition to default-secure above, regardless of
    # bind. `standard` (default) leaves the permissive laptop behaviour untouched.
    if profile == "production":
        from ._production_profile import assert_production_ready  # noqa: PLC0415

        assert_production_ready(
            workspace_path, auth=_auth, insecure=insecure, cors_origins=cors_origins
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
        app.state.workspace_path = workspace_path  # for GET /fleet/state (reads artifact content)
        _warn_if_identity_is_not_a_role_member(_auth, runtime)
        _wire_storage(app, workspace_path, runtime)

        # The inbound event seam. It had one consumer — the bundled sequencer — and its queue left
        # with it, so nothing is wired by default: an application that wants correlated webhook
        # events sets `app.state.event_signal` to its own sink
        # (`docs/notes/pipeline-deprecation.md`).
        app.state.pipeline_signal = getattr(app.state, "event_signal", None)

        # Parse server config from workspace.yaml
        cfg = _parse_server_config(runtime.workspace)
        app.state.server_config = cfg
        app.state.job_semaphore = asyncio.Semaphore(cfg.max_concurrent)
        # API tier of the API/worker split: POST /run enqueues, a worker executes.
        app.state.enqueue_only = enqueue_only
        app.state.queue_max_depth = queue_max_depth
        # Close jobs a previous process left in flight. A job started via `POST /run/{topology}`
        # runs as a task in THIS process; when the process dies the task dies with it, and nothing
        # reconciled the durable row — it sat at `running` for ever, indistinguishable from work
        # still in progress. Pipeline runs recover themselves (a stale claim is reclaimable); these
        # never did.
        #
        # Bounded by the job timeout, not by "everything running right now": several instances can
        # share one Postgres store, and a blanket sweep would close another live instance's jobs.
        _sweep_stale_jobs(app.state.store, cfg.timeout_seconds)

        logger.info(
            "Server config: max_concurrent=%d, timeout=%ds, mcp_enabled=%s",
            cfg.max_concurrent,
            cfg.timeout_seconds,
            cfg.mcp_enabled,
        )

        # Named, not counted: an operator who reads "3 unreachable" still has to go looking, and
        # going looking is what nobody did for five bugs running
        # (design/details/declared-but-unreachable.md).
        _log_unreachable(runtime)

        # Wire OpenTelemetry trace export (design: runtime/otel-trace-export): a run's spans go to
        # the configured OTLP collector so Jaeger/Grafana show it. No-op unless SWARMKIT_OTEL_* set.
        # Name the OTel service after this instance's workspace so a fleet is distinguishable in
        # Jaeger (each instance = its own service) — unless the operator set a custom service_name.
        tel_cfg = load_telemetry_config()
        if tel_cfg.service_name == "swarmkit":
            tel_cfg = replace(tel_cfg, service_name=runtime.telemetry_service_name)
        if configure_telemetry(tel_cfg).enabled:
            logger.info(
                "Telemetry enabled: service=%s exporter=%s endpoint=%s",
                tel_cfg.service_name,
                tel_cfg.exporter,
                tel_cfg.endpoint or "(default)",
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

        # The MCP transport's session manager lives exactly as long as the app does.
        async with mcp_session_lifespan(app):
            yield
        await scheduler.stop()
        # The *current* runtime — a reload may have replaced the one this lifespan booted.
        await app.state.runtime.close()

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

    @app.get("/auth-info")
    async def auth_info() -> dict[str, object]:
        """Unauthenticated: advertise the server's auth mode (+ OIDC issuer/audience for jwt) so a
        client renders the right login gate before it holds a token."""
        return _auth.public_info()

    @app.get("/whoami")
    async def whoami(request: Request) -> dict[str, object]:
        """The *authenticated* caller's identity — as opposed to ``/auth-info``, which is public and
        describes only the server's auth mode.

        A front-end resolving a multi-party approval needs this: the resolver is the session
        identity (design/details/pipeline-gate-approval-ui.md), so without it a UI cannot tell an
        operator which capacity they are about to act in, and every role-task looks equally
        actionable until the server 403s one.
        """
        identity = getattr(request.state, "identity", None)
        return {
            "client_id": getattr(identity, "client_id", "") or "anonymous",
            "client_name": getattr(identity, "client_name", "") or "Anonymous",
            "provider": getattr(identity, "provider", "") or _auth.mode,
            "scopes": sorted(getattr(identity, "scopes", frozenset())),
            "mode": _auth.mode,
        }

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
        # /health + /auth-info are public (the latter tells an unauthenticated client which login
        # gate to render); /fleet/register + /fleet/refresh authenticate with their own token
        # (enrollment token / current membership key) inside the route (design 19), so they bypass
        # the transport-token seam here.
        if request.url.path in (
            "/health",
            "/auth-info",
            "/fleet/register",
            "/fleet/refresh",
            A2A_WELL_KNOWN_PATH,
        ):
            return await call_next(request)
        # A CORS preflight carries no credentials by design — the browser sends it BEFORE the
        # request that will carry `Authorization`. Answering it 401 here meant a portal on another
        # origin could never reach an api_key-protected serve: every call died in preflight with
        # "Failed to fetch". The CORS middleware (inside this one) answers preflights; a request
        # that follows is authenticated like any other.
        # A webhook is called by a third party (GitHub, CI) that cannot hold a serve API key; it
        # authenticates with the signature its trigger declares (`config.auth`), checked by the
        # route. Behind the API-key gate every signed delivery was a 401 before the signature was
        # even read — the moment `server.auth` was turned on, every webhook trigger stopped.
        is_preflight = (
            request.method == "OPTIONS" and "access-control-request-method" in request.headers
        )
        is_signed_webhook = (
            request.method == "POST"
            and request.url.path.startswith("/hooks/")
            and _webhook_has_its_own_auth(request.app, request.url.path.removeprefix("/hooks/"))
        )
        if is_preflight or is_signed_webhook:
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
            # A fleet reads its instance's state with the membership credential it was issued at
            # enrollment (design 19), not a serve transport token. When the transport seam rejects
            # the bearer, fall back to membership auth for the fleet-read routes — a valid
            # membership key (monitor+ scope) authorizes the read. Non-fleet routes stay denied.
            if _membership_authenticates(request, request.method, request.url.path):
                return await call_next(request)
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
    _register_event_routes(app)
    # On app.state so the review routes can resume a run whose gate was just satisfied, without
    # importing the job routes (extracting-the-channels.md: a resolved gate resumes its run).
    app.state.job_store = job_store
    _register_job_routes(app, job_store)
    _register_conversation_routes(app, workspace_path)
    # Before the CRUD routes: `GET /api/skills/check` must beat `GET /api/skills/{skill_id}`.
    _register_skill_registry_routes(app)
    _register_crud_routes(app, ArtifactService(workspace_path))
    _register_config_routes(app, WorkspaceConfigService(workspace_path))
    _register_event_stream_routes(app)
    _register_oauth_routes(
        app,
        OAuthService(store=TokenStore(workspace_path), pending=PendingLogins()),
    )
    _register_review_routes(app, workspace_path)
    _register_fleet_routes(app)
    _register_memory_routes(app)
    # Registered unconditionally; each route answers 404 until `server.a2a.enabled` is true, so
    # a workspace can flip it on with a reload and not a restart.
    _register_a2a_routes(app, _auth, workspace_path)

    if _mcp_available:
        _mount_mcp(app)
    else:
        logger.warning("mcp package not installed; /mcp endpoint disabled")

    # An unmatched /api path must get an API answer, not the app shell. The portal mounts at "/"
    # and catches everything the API did not match, which includes a GET to a POST-only route:
    # Starlette would have answered 405, and the mount turned that into the SPA (or a bare 404).
    # "Wrong method" then read as "no such endpoint" — `GET /api/reload` was reported as exactly
    # that. Registered before the portal so it wins, and after every real route so it never
    # shadows one.
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def _unmatched_api(rest: str, request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": f"no {request.method} route for /api/{rest}",
                # The common case, and the one that costs an afternoon: the path exists, the method
                # does not. Say so rather than leaving a reader to conclude the endpoint is missing.
                "hint": "some endpoints are POST-only because they mutate — /api/reload among them",
            },
        )

    # The static web portal is the catch-all — mounted last so every API route + /mcp win first.
    if not mount_webui(app):
        logger.info("web portal not installed; API only (pip install 'swarmkit-runtime[ui]')")

    return app
