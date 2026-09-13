"""Portal endpoints for the infrastructure half of `workspace.yaml`.

Peer to `_routes_crud`, which covers artifacts. See `_workspace_config` for why this exists and
what it refuses to expose.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.server._services import ServiceError
from swarmkit_runtime.server._workspace_config import WorkspaceConfigService


def _reload(request: Request, service: WorkspaceConfigService) -> None:
    """Swap in a runtime rebuilt from the edited file.

    Without this a saved connection would apply on the next restart, and Minder's lesson is that a
    settings screen whose changes need a restart is a settings screen people stop trusting.
    """
    try:
        request.app.state.runtime = WorkspaceRuntime.from_workspace_path(service.workspace_path)
    except Exception:
        return


def _register_config_routes(app: FastAPI, service: WorkspaceConfigService) -> None:
    """Read and write `credentials` and `mcp_servers` — workspace.yaml's infrastructure half."""

    @app.get("/api/workspace/config")
    async def get_config() -> dict[str, Any]:
        """The editable infrastructure sections of workspace.yaml — credentials and MCP servers —
        with secrets masked.
        """
        try:
            return service.read()
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @app.put("/api/workspace/config/{section}/{entry_id}")
    async def put_entry(section: str, entry_id: str, request: Request) -> dict[str, Any]:
        """Create or replace one entry in a workspace.yaml section (`credentials` or `mcp_servers`);
        the workspace reloads when the file changed.
        """
        body = await request.json()
        try:
            result = service.upsert(section, entry_id, body)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        if result.get("saved"):
            _reload(request, service)
        return result

    @app.delete("/api/workspace/config/{section}/{entry_id}")
    async def delete_entry(section: str, entry_id: str, request: Request) -> dict[str, Any]:
        """Remove one entry from a workspace.yaml section; the workspace reloads if the file
        changed.
        """
        try:
            result = service.delete(section, entry_id)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        if result.get("saved"):
            _reload(request, service)
        return result
