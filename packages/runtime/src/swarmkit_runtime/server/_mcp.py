"""Optional MCP mount + the boot-time lifespan factories (MCP session, cron scheduler).
Kept apart from the app factory so ``create_app`` reads as pure wiring."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from typing import Any

from fastapi import FastAPI, Request

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.triggers import TriggerScheduler

from ._config import ServerCfg
from ._jobs import JobStore, _start_job

logger = logging.getLogger("swarmkit.server")

# ---- MCP optional import ----------------------------------------------------

_mcp_available = importlib.util.find_spec("mcp") is not None


def _mount_mcp(app: FastAPI) -> None:
    """Set up MCP server and mount on the FastAPI app.

    Called only when the ``mcp`` package is importable.
    """
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

    mcp_server = FastMCP("swarmkit")
    _tools_registered = False

    @app.middleware("http")
    async def _register_mcp_tools(request: Request, call_next):  # type: ignore[no-untyped-def]
        nonlocal _tools_registered
        if not _tools_registered and hasattr(request.app.state, "runtime"):
            rt: WorkspaceRuntime = request.app.state.runtime
            for name, topo in rt.workspace.topologies.items():
                description = topo.root.source_archetype or f"Run topology {name}"

                def _make_tool_fn(topo_name: str, desc: str, app_ref: FastAPI) -> None:
                    async def _run(input: str) -> str:
                        runtime: WorkspaceRuntime = app_ref.state.runtime
                        result = await runtime.run(topo_name, input)
                        return result.output

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

            _tools_registered = True
            logger.info(
                "MCP tools registered for %d topologies",
                len(rt.workspace.topologies),
            )

        return await call_next(request)

    try:
        mcp_app = mcp_server.streamable_http_app()
        app.mount("/mcp", mcp_app)
        logger.info("MCP endpoint mounted at /mcp")
    except Exception:
        logger.warning("Failed to mount MCP endpoint", exc_info=True)


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

    async def _fire_trigger(topology_name: str, source: str) -> None:
        rt: WorkspaceRuntime = app.state.runtime
        sema: asyncio.Semaphore | None = getattr(app.state, "job_semaphore", None)
        server_cfg: ServerCfg = getattr(app.state, "server_config", ServerCfg())
        job = await job_store.create(topology_name, source)
        _start_job(
            job_store,
            job,
            rt,
            max_steps=10,
            timeout_seconds=server_cfg.timeout_seconds,
            semaphore=sema,
        )
        logger.info(
            "Trigger fired topology=%r job_id=%s source=%r",
            topology_name,
            job.id,
            source,
        )

    scheduler = TriggerScheduler(trigger_configs, _fire_trigger)
    await scheduler.start()
    return scheduler
