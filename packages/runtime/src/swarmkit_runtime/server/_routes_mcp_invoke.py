"""``POST /api/mcp/{server_id}/invoke`` — direct MCP tool invocation with per-user credential
resolution, for external clients that need to call a tool without going through a topology run.

Composes existing internals with no new machinery:

- The auth middleware in :mod:`._app` has already validated the bearer and put the
  :class:`swarmkit_runtime.auth.AuthIdentity` on ``request.state.identity``.
- ``X-Owner`` names the identity whose stored credential should be resolved for this call. It is
  set into :mod:`swarmkit_runtime._principal` for the request's duration; a ``per-user`` credential
  then resolves to that identity's token via :mod:`swarmkit_runtime.credentials._service`.
- :func:`swarmkit_runtime.mcp._governed.governed_mcp_call` handles the permission check, the tool
  invocation and the audit record — the same path a topology's LLM tool-call takes.

**The endpoint never returns the resolved OAuth token in the response.** It invokes the tool
server-side and returns only the tool's result. That is what preserves the credential invariant
letting external clients (the DOT app, any equivalent) hold no third-party tokens.

Design: [design/details/dot-app.md](../../../../design/details/dot-app.md); issue #994.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from swarmkit_runtime._principal import principal_scope
from swarmkit_runtime.mcp._governed import MCPCallDenied, governed_mcp_call

from ._helpers import _get_runtime

#: The scope a caller needs to supply an ``X-Owner`` that names an identity other than their own.
#: A caller may always supply their own ``client_id`` as ``X-Owner`` (or omit it and pick up the
#: caller identity). Naming somebody else is a widening move that requires an admin scope.
ADMIN_SCOPE = "mcp:invoke:any-owner"


class InvokeRequest(BaseModel):
    """The JSON body for :func:`invoke_mcp_tool`."""

    #: The MCP tool name, as declared by the server. Required, non-empty.
    tool: str = Field(..., min_length=1)
    #: Tool arguments. Whatever shape the tool declares; forwarded unchanged.
    arguments: dict[str, Any] = Field(default_factory=dict)


def _register_mcp_invoke_routes(app: FastAPI) -> None:
    """Register the direct MCP invocation route on ``app``."""

    @app.post("/api/mcp/{server_id}/invoke")
    async def invoke_mcp_tool(
        server_id: str, request: Request, body: InvokeRequest
    ) -> dict[str, Any]:
        """Invoke an MCP tool on the named server, with the owner's credential resolved by the
        runtime. Returns ``{"result": ...}`` — the tool's own response, never a token.

        Errors:

        - **401** — no authenticated caller. Should be caught by middleware first; a 401 here
          means the middleware admitted an unauthenticated request, which is a bug.
        - **403** — ``X-Owner`` names a different identity than the caller and the caller does
          not hold :data:`ADMIN_SCOPE`; or governance refused the call.
        - **404** — unknown ``server_id``, or the workspace has no MCP servers configured.
        - **400** — the body is malformed (pydantic validation).

        The tool's own errors (a Gmail 429, a Calendar 404 on an event id) pass through as a
        200 response whose ``result.isError`` is ``True``, matching how an LLM tool-call sees
        them. The route only translates transport / governance / not-found failures to HTTP
        error codes.
        """
        identity = getattr(request.state, "identity", None)
        if identity is None:
            raise HTTPException(status_code=401, detail="unauthenticated")

        owner_header = request.headers.get("X-Owner")
        if (
            owner_header
            and owner_header != identity.client_id
            and ADMIN_SCOPE not in identity.scopes
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"X-Owner {owner_header!r} names a different identity than the caller "
                    f"({identity.client_id!r}); this requires the {ADMIN_SCOPE!r} scope."
                ),
            )
        # Empty owner_header falls back to the caller's own identity — the natural per-user case
        # where the calling client and the credential owner are the same person.
        principal = owner_header or identity.client_id

        runtime = _get_runtime(request)
        mcp_manager = runtime.mcp_manager
        if mcp_manager is None:
            raise HTTPException(status_code=404, detail="workspace has no mcp_servers configured")

        # Fail with a clear 404 for an unknown server_id rather than letting the invoke path emit
        # a generic error. ``get_permission`` returns ``"cautious"`` for an unconfigured server
        # (its own default), so probe via the manager's ``server_ids`` registry instead.
        if server_id not in mcp_manager.server_ids:
            raise HTTPException(status_code=404, detail=f"unknown mcp_server: {server_id!r}")

        try:
            with principal_scope(principal):
                response = await governed_mcp_call(
                    mcp_manager,
                    runtime.governance,
                    # Attribution goes to the calling client rather than a generic
                    # "external" — the audit record then names who actually made the call
                    # (per the gateway's own convention that shared servers must not attribute
                    # every call to one agent).
                    agent_id=identity.client_id,
                    server_id=server_id,
                    tool_name=body.tool,
                    arguments=body.arguments,
                )
        except MCPCallDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        return {"result": _serialize_tool_result(response.data)}


def _serialize_tool_result(data: Any) -> dict[str, Any]:
    """Turn an MCP ``CallToolResult`` into a JSON-serialisable dict, exactly the surface a caller
    would see if it had made the tool call itself.

    The MCP SDK's result carries ``content`` (a list of :class:`ContentBlock`), ``isError``, and
    optional ``structuredContent``. Serialise each with the ``model_dump`` a pydantic block
    exposes; fall back to plain text otherwise. Nothing about credentials, transport, or session
    identity is present in this shape — the same isolation the underlying MCP client already
    enforces.
    """
    return {
        "content": [_serialize_block(b) for b in getattr(data, "content", []) or []],
        "isError": bool(getattr(data, "isError", False)),
        "structuredContent": getattr(data, "structuredContent", None),
    }


def _serialize_block(block: Any) -> dict[str, Any]:
    """One ContentBlock → dict. ``model_dump`` covers pydantic-based blocks (the SDK's default);
    the plain-text fallback preserves whatever a non-model block emits without inventing shape."""
    if hasattr(block, "model_dump"):
        dumped: dict[str, Any] = block.model_dump(exclude_none=True)
        return dumped
    return {"text": str(block)}
