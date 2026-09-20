"""Optional MCP mount + the boot-time lifespan factories (MCP session, cron scheduler).
Kept apart from the app factory so ``create_app`` reads as pure wiring."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
import typing
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.triggers import TriggerScheduler

from ._config import ServerCfg
from ._jobs import JobStore

logger = logging.getLogger("swarmkit.server")

# ---- MCP optional import ----------------------------------------------------

_mcp_available = importlib.util.find_spec("mcp") is not None


_PIPELINE_MODES = ("emit", "advance", "skip")


def _register_pipeline_event_tool(mcp_server: Any, app: FastAPI) -> None:
    """Register the governed ``submit_pipeline_event`` MCP tool.

    For agents, IDEs, and bots (design/details/pipeline-triggering.md §"MCP tool"): a typed,
    audited front door onto the pipeline signal seam. It routes through the *same*
    ``_ingress_pipeline_event`` guardrail the HTTP endpoint uses — authorize → audit → deliver —
    so ``advance`` / ``skip`` still require the reserved human-identity scope. The MCP caller is a
    transport/agent principal (``mcp``), which under real governance holds neither reserved scope,
    so an agent may ``emit`` an authorised event but can never advance or skip on its own authority.
    """
    from swarmkit_runtime.triggers import (  # noqa: PLC0415
        PipelineIngressError,
        PipelineMode,
        PipelineSignal,
        _ingress_pipeline_event,
    )

    async def submit_pipeline_event(
        pipeline: str,
        correlation_id: str,
        event: str,
        mode: str = "emit",
    ) -> str:
        """Signal a structured pipeline event to the orchestrator (emit | advance | skip)."""
        if mode not in _PIPELINE_MODES:
            return f"error: mode must be one of {_PIPELINE_MODES}, got {mode!r}"
        runtime: WorkspaceRuntime | None = getattr(app.state, "runtime", None)
        if runtime is None:
            return "error: workspace not loaded yet"
        seam: PipelineSignal | None = getattr(app.state, "pipeline_signal", None)
        try:
            await _ingress_pipeline_event(
                governance=runtime.governance,
                signal=seam,
                correlation_id=correlation_id,
                event=event,
                mode=typing.cast(PipelineMode, mode),
                actor_identity="mcp",
                source=f"mcp:{pipeline}",
                source_event_id=None,
            )
        except PipelineIngressError as exc:
            return f"error ({exc.status_code}): {exc.detail}"
        return f"delivered event {event!r} for {correlation_id!r} on {pipeline!r} (mode={mode})"

    mcp_server.add_tool(
        submit_pipeline_event,
        name="submit_pipeline_event",
        description=(
            "Signal a structured pipeline event to the orchestrator. mode=emit sends an ordinary "
            "authorised event; mode=advance/skip start or jump a stage and need an operator's "
            "reserved scope (governance-gated, audited)."
        ),
    )


async def _run_as_job(app: FastAPI, topology_name: str, user_input: str) -> str:
    """Run a topology for an MCP tool call — through the same service every other caller uses.

    This called ``WorkspaceRuntime.run`` directly, which is the same mistake the trigger path made
    and had already fixed above: a run started that way is not a job. It gets no durable row, so it
    is invisible in ``/jobs/history`` the moment it ends; no canary routing, so a topology being
    canaried is bypassed by whichever front door the caller happened to use; no capacity gate, so
    ``jobs.max_concurrent`` means nothing here and an assistant can start unlimited concurrent runs
    on an instance that is carefully bounded everywhere else; and no `source`, so nobody can tell
    which runs came from an assistant.

    The CLI, ``POST /run``, A2A, triggers and now MCP all start work the same way. A second path
    into the runtime is a second set of rules to keep in step, and it drifts silently — the only
    symptom is a run that behaves differently depending on which door it came through.

    The tool still answers with the output, so it waits for the job it started.
    """
    from ._services import JobService, ServiceError  # noqa: PLC0415

    job_store: JobStore = app.state.job_store
    cfg: ServerCfg = getattr(app.state, "server_config", ServerCfg())
    try:
        job = await JobService(job_store).start(
            rt=app.state.runtime,
            canary=getattr(app.state, "canary_router", None),
            store=getattr(app.state, "store", None),
            cfg=cfg,
            semaphore=getattr(app.state, "job_semaphore", None),
            topology_name=topology_name,
            user_input=user_input,
            max_steps=10,
            # Attributable in the portal and in `tasks/list`, the same way `a2a` runs are.
            source="mcp",
        )
    except ServiceError as exc:
        # The tool's contract is a string; a refusal is an answer, not a transport error.
        return f"Could not start {topology_name!r}: {exc}"

    terminal = {"completed", "failed", "stopped", "interrupted", "deferred"}
    deadline = time.monotonic() + max(1, cfg.timeout_seconds) + 5
    while job.status not in terminal and time.monotonic() < deadline:
        await asyncio.sleep(0.05)

    if job.status == "completed":
        return job.output or ""
    if job.status == "deferred":
        # Parked on a human gate. Saying so with the id is the only useful answer: an assistant
        # cannot resolve the gate (approval scopes are un-grantable), but a person can.
        return (
            f"Run {job.id} is waiting on a human gate and will continue once it is resolved. "
            f"Nothing further to do from here."
        )
    if job.status in terminal:
        return f"Run {job.id} {job.status}: {job.error or 'no error recorded'}"
    return f"Run {job.id} is still running; poll GET /jobs/{job.id} for its result."


def _mount_mcp(app: FastAPI) -> None:
    """Set up MCP server and mount on the FastAPI app.

    Called only when the ``mcp`` package is importable.
    """
    from swarmkit_runtime.mcp._sdk_compat import MCPServerClass  # noqa: PLC0415

    # The SDK switches on DNS-rebinding protection for a loopback host and then refuses any
    # `Host:` it does not recognise with a 421 — including `127.0.0.1` without a port, and every
    # name a container or reverse proxy is reached by. Serve already decides what it binds to and
    # who may call it (`server.auth`); the transport must not second-guess that.
    try:
        from mcp.server.transport_security import (  # noqa: PLC0415
            TransportSecuritySettings,
        )

        mcp_server = MCPServerClass(
            "swarmkit",
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
    except (ImportError, TypeError):  # an SDK without the setting has no such check
        mcp_server = MCPServerClass("swarmkit")
    _tools_registered = False

    @app.middleware("http")
    async def _register_mcp_tools(request: Request, call_next):  # type: ignore[no-untyped-def]
        nonlocal _tools_registered
        if not _tools_registered and hasattr(request.app.state, "runtime"):
            rt: WorkspaceRuntime = request.app.state.runtime
            for name, topo in rt.workspace.topologies.items():
                if "@" in name:  # a version key (canary), not a tool name — see mcp/_serve.py
                    continue
                description = topo.root.source_archetype or f"Run topology {name}"

                def _make_tool_fn(topo_name: str, desc: str, app_ref: FastAPI) -> None:
                    async def _run(input: str) -> str:
                        return await _run_as_job(app_ref, topo_name, input)

                    mcp_server.add_tool(
                        _run,
                        name=f"run_{topo_name}",
                        description=desc,
                    )

                _make_tool_fn(name, description, request.app)

                def _make_resource(topo_name: str, desc: str) -> None:
                    @mcp_server.resource(f"topology://{topo_name}")
                    async def _resource() -> str:
                        return f"Topology: {topo_name} -- {desc}"

                _make_resource(name, description)

            _register_pipeline_event_tool(mcp_server, request.app)

            _tools_registered = True
            logger.info(
                "MCP tools registered for %d topologies",
                len(rt.workspace.topologies),
            )

        return await call_next(request)

    try:
        # The streamable-HTTP transport serves at `settings.streamable_http_path` INSIDE the
        # mounted app — `/mcp` by default, which under a `/mcp` mount made the real endpoint
        # `/mcp/mcp`. Serve it at the mount root so a client points at `/mcp/`.
        settings = getattr(mcp_server, "settings", None)
        if settings is not None and hasattr(settings, "streamable_http_path"):
            settings.streamable_http_path = "/"
        mcp_app = mcp_server.streamable_http_app()
        app.mount("/mcp", mcp_app)
        # The transport's session manager must be RUN for the endpoint to answer at all; the
        # lifespan in `_app.py` enters it (`mcp_session_lifespan`). Without that every request
        # was a 500 — "Task group is not initialized" — and nothing said so at startup.
        app.state.mcp_server = mcp_server
        logger.info("MCP endpoint mounted at /mcp")
    except Exception:
        logger.warning("Failed to mount MCP endpoint", exc_info=True)


@asynccontextmanager
async def mcp_session_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the streamable-HTTP session manager for the life of the app (no-op without one)."""
    mcp_server = getattr(app.state, "mcp_server", None)
    manager = getattr(mcp_server, "session_manager", None) if mcp_server is not None else None
    if manager is None:
        yield
        return
    async with manager.run():
        yield


async def _boot_mcp(runtime: WorkspaceRuntime, cfg: ServerCfg) -> None:
    """Start MCP servers at boot when enabled."""
    if cfg.mcp_enabled:
        try:
            await runtime.start_session()
            logger.info("MCP servers started at boot")
        except Exception:
            logger.warning(
                "MCP server boot failed; runs will manage per-invocation",
                exc_info=True,
            )
    else:
        logger.info("MCP server boot disabled by server.mcp.enabled=false")


async def _start_scheduler(
    app: FastAPI,
    job_store: JobStore,
    trigger_configs: list[dict[str, Any]],
) -> TriggerScheduler:
    """Create and start a TriggerScheduler wired to the app's job store."""

    async def _fire_trigger(topology_name: str, source: str, user_input: str = "") -> None:
        # Through the SAME service as `POST /run/{topology}`: the run gets the canary version,
        # the durable job row and the capacity gate. Fired directly, a scheduled run was an
        # in-memory job with `source` as its input — invisible in `/jobs/history` once it ended,
        # and its input was the literal string `trigger:<id>`.
        from ._services import JobService, ServiceError  # noqa: PLC0415

        rt: WorkspaceRuntime = app.state.runtime
        sema: asyncio.Semaphore | None = getattr(app.state, "job_semaphore", None)
        server_cfg: ServerCfg = getattr(app.state, "server_config", ServerCfg())
        try:
            job = await JobService(job_store).start(
                rt=rt,
                canary=getattr(app.state, "canary_router", None),
                store=getattr(app.state, "store", None),
                cfg=server_cfg,
                semaphore=sema,
                topology_name=topology_name,
                user_input=user_input or source,
                max_steps=10,
                source=source,
            )
        except ServiceError as exc:
            logger.warning("Trigger %r could not start %r: %s", source, topology_name, exc)
            return
        logger.info(
            "Trigger fired topology=%r job_id=%s source=%r",
            topology_name,
            job.id,
            source,
        )

    scheduler = TriggerScheduler(trigger_configs, _fire_trigger)
    await scheduler.start()
    return scheduler
