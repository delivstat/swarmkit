"""Control-plane panel API — the app factory. Request models live in ``_schemas``, the
connector-fn type aliases in ``_fntypes``, and the route groups in ``_routes_*``; this module
wires them together (auth + CORS middleware, default-secure guard, health, mounts).

See design/details/control-plane/13-connector-registry.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from swarmkit_control_plane._aggregation import AggregationStore
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._auth import authenticate, authorize
from swarmkit_control_plane._connector import (
    fetch_artifacts,
    fetch_capabilities,
    fetch_jobs,
    fetch_manifest,
    fetch_state,
    leave,
    refresh,
    register,
    run_authoring,
    run_eval,
)
from swarmkit_control_plane._credential_store import CredentialStore
from swarmkit_control_plane._deploy import push_artifact
from swarmkit_control_plane._fleet_identity import FleetIdentity
from swarmkit_control_plane._fntypes import (
    AuthorFn,
    DeployFn,
    EvalFn,
    JobsFn,
    LeaveFn,
    RefreshFn,
    RegisterFn,
    StateArtifactsFn,
    StateFn,
    StateManifestFn,
    VerifyFn,
)
from swarmkit_control_plane._join_code_store import JoinCodeStore
from swarmkit_control_plane._oidc import OidcVerifier
from swarmkit_control_plane._proposals import ProposalStore
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._routes_artifacts import (
    _mount_artifacts,
    _mount_deploy,
    _mount_observability,
)
from swarmkit_control_plane._routes_growth import (
    _mount_aggregation,
    _mount_growth,
    _mount_proposals,
)
from swarmkit_control_plane._routes_registry import (
    _mount_command_queue,
    _mount_config,
    _mount_fleet_identity,
    _mount_instances,
    _mount_join,
    _mount_membership,
    _mount_register,
    _mount_state,
    _mount_token_routes,
)
from swarmkit_control_plane._service import DeployService, GrowthService
from swarmkit_control_plane._state_store import InstanceStateStore


def create_app(
    registry: SqliteRegistry,
    *,
    verify: VerifyFn = fetch_capabilities,
    fetch_state: StateFn = fetch_state,
    fetch_manifest: StateManifestFn = fetch_manifest,
    fetch_artifacts: StateArtifactsFn = fetch_artifacts,
    register_fn: RegisterFn = register,
    refresh_fn: RefreshFn = refresh,
    leave_fn: LeaveFn = leave,
    cors_origins: list[str] | None = None,
    operator_tokens: list[str] | None = None,
    oidc: OidcVerifier | None = None,
    aggregation: AggregationStore | None = None,
    observability: dict[str, str] | None = None,
    artifacts: ArtifactStore | None = None,
    proposals: ProposalStore | None = None,
    deploy: DeployFn = push_artifact,
    jobs: JobsFn = fetch_jobs,
    author: AuthorFn = run_authoring,
    eval_run: EvalFn = run_eval,
    host: str = "127.0.0.1",
    allow_open: bool = False,
) -> FastAPI:
    """Build the control-plane API. *verify* is injectable for testing.

    *cors_origins* are the exact browser origins allowed to call the panel (the fleet UI in a
    split-origin deploy). CORS is entirely config-driven — no origin is allowed unless listed here
    (CLI: --cors-origin / $SWARMKIT_CONTROL_PLANE_CORS_ORIGINS). For local dev, pass the UI's
    origin explicitly, e.g. --cors-origin http://localhost:3000.

    Auth is enabled when *operator_tokens* and/or *oidc* are configured; then every route except
    /health requires a bearer — an operator token (full access), a Mode B instance's per-instance
    token (scoped to its poll + result routes), or an OIDC JWT (human→panel, authenticates as an
    operator). When neither is set, the panel runs open (no auth) for local dev.
    """
    app = FastAPI(title="SwarmKit control plane")
    # Share the registry's engine so all stores use one connection pool + one database
    # (dialect-agnostic: works for both the SQLite file and a shared Postgres).
    agg = aggregation or AggregationStore(registry.engine)
    arts = artifacts or ArtifactStore(registry.engine)
    props = proposals or ProposalStore(registry.engine)
    state_store = InstanceStateStore(registry.engine)
    cred_store = CredentialStore(registry.engine)  # membership secrets, encrypted at rest
    join_store = JoinCodeStore(registry.engine)  # one-time Mode B join codes
    fleet_identity = FleetIdentity(registry.engine)  # this panel's Ed25519 identity (design 21)
    growth = GrowthService(registry, props, arts, author, eval_run)
    deploy_svc = DeployService(registry, arts, agg, deploy, cred_store)
    ops = [t for t in (operator_tokens or []) if t]

    # Default-secure: an unauthenticated panel can mint serve tokens and deploy artifacts,
    # so refuse to serve open on a non-loopback bind unless the operator explicitly opts in
    # (allow_open / --insecure-no-auth). Loopback dev stays open for convenience.
    _loopback = {"127.0.0.1", "::1", "localhost", ""}
    if not (ops or oidc is not None) and not allow_open and host not in _loopback:
        raise RuntimeError(
            f"refusing to start an unauthenticated control plane on a non-loopback bind "
            f"({host!r}). Configure --operator-token and/or --oidc-*, or pass "
            f"--insecure-no-auth to override."
        )

    # Auth is registered before CORS so CORS stays the outermost layer (preflight + 401/403
    # responses still carry the configured CORS headers for the browser to read).
    if ops or oidc is not None:

        @app.middleware("http")
        async def auth_middleware(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            # /fleet/join authenticates with its one-time join code inside the route (design 19,
            # Mode B) — the joining instance has no panel credential yet, so it bypasses the seam
            # here (mirrors serve's /fleet/register). /fleet/join-code stays operator-gated.
            if request.url.path in ("/health", "/fleet/join") or request.method == "OPTIONS":
                return await call_next(request)
            header = request.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else ""
            principal = authenticate(token, ops, registry, oidc)
            if principal is None:
                return JSONResponse({"detail": "missing or invalid bearer token"}, status_code=401)
            if not authorize(principal, request.method, request.url.path):
                return JSONResponse(
                    {"detail": "this token is not allowed to call that route"}, status_code=403
                )
            request.state.principal = principal  # let handlers scope writes to the caller
            return await call_next(request)

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    _mount_instances(app, registry, verify, jobs, author)
    _mount_token_routes(app, registry, verify)
    _mount_state(app, registry, state_store, arts, fetch_state, fetch_manifest, fetch_artifacts)
    _mount_register(app, registry, state_store, cred_store, register_fn, refresh_fn, fleet_identity)
    _mount_membership(app, registry, cred_store, leave_fn)
    _mount_fleet_identity(app, fleet_identity)
    _mount_join(app, registry, join_store, state_store)
    _mount_command_queue(app, registry)
    _mount_aggregation(app, agg)
    _mount_observability(app, observability or {})
    _mount_artifacts(app, arts)
    _mount_proposals(app, growth)
    _mount_growth(app, growth)
    _mount_deploy(app, deploy_svc)
    _mount_config(
        app,
        {
            "version": _cp_version(),
            "auth": {
                "operator_tokens": bool(ops),
                "oidc": {
                    "enabled": oidc is not None,
                    "issuer": oidc.issuer if oidc else "",
                    "audience": oidc.audience if oidc else "",
                },
            },
            "cors_origins": cors_origins or [],
            "observability": observability or {},
        },
    )
    return app


def _cp_version() -> str:
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    try:
        return version("swarmkit-control-plane")
    except PackageNotFoundError:
        return "unknown"
