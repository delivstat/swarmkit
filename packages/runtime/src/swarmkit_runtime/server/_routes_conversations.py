"""Conversation CRUD + the streaming send-message endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from ._helpers import (
    _get_runtime,
)
from ._schemas import (
    CreateConversationRequest,
    SendMessageRequest,
)

logger = logging.getLogger("swarmkit.server")


def _register_conversation_routes(app: FastAPI, workspace_path: Path) -> None:  # noqa: PLR0915
    """Register conversation CRUD endpoints."""

    @app.post("/conversations")
    async def create_conversation(
        body: CreateConversationRequest, request: Request
    ) -> dict[str, str]:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        conv = manager.create(body.topology)
        return {"id": conv.id, "topology": conv.topology_name}

    @app.get("/conversations")
    async def list_conversations_endpoint(
        request: Request,
    ) -> list[dict[str, str]]:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        return manager.list_conversations()

    @app.get("/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        conv = manager.resume(conversation_id)
        if conv is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation '{conversation_id}' not found",
            )
        return {
            "id": conv.id,
            "topology": conv.topology_name,
            "turns": [
                {
                    "role": t.role,
                    "content": t.content,
                    "timestamp": t.timestamp,
                }
                for t in conv.turns
            ],
            "created_at": conv.created_at,
            "updated_at": conv.updated_at,
        }

    @app.post("/conversations/{conversation_id}/messages")
    async def send_message(
        conversation_id: str, body: SendMessageRequest, request: Request
    ) -> StreamingResponse:
        from swarmkit_runtime._conversation import ConversationManager  # noqa: PLC0415

        rt = _get_runtime(request)
        manager = ConversationManager(rt, workspace_path)
        conv = manager.resume(conversation_id)
        if conv is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conversation '{conversation_id}' not found",
            )

        async def stream_response() -> AsyncGenerator[str]:
            from swarmkit_runtime.langgraph_compiler._helpers import (  # noqa: PLC0415
                progress_listener,
            )

            progress_lines: list[str] = []

            def on_progress(msg: str) -> None:
                text = msg.strip()
                if text:
                    progress_lines.append(text)

            sent = 0
            # Register the listener in *this* request's context, then spawn the run — the
            # task inherits the context, so progress stays scoped to this conversation and
            # never bleeds into another concurrent stream.
            with progress_listener(on_progress):
                send_task = asyncio.create_task(manager.send(conv, body.message))
                while not send_task.done():
                    await asyncio.sleep(0.3)
                    new_lines = progress_lines[sent:]
                    for line in new_lines:
                        yield f"data: {json.dumps({'type': 'progress', 'text': line})}\n\n"
                        sent += 1

            for line in progress_lines[sent:]:
                yield f"data: {json.dumps({'type': 'progress', 'text': line})}\n\n"

            try:
                result = send_task.result()
                events = [
                    {
                        "event_type": e.event_type,
                        "agent_id": e.agent_id,
                        "timestamp": e.timestamp,
                        "duration_ms": e.payload.get("duration_ms"),
                        "model": e.payload.get("model"),
                        "tokens": e.payload.get("usage_tokens"),
                    }
                    for e in result.events
                ]
                usage_data = None
                if result.usage:
                    usage_data = {
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "total_tokens": result.usage.total_tokens,
                        "by_model": result.usage.by_model,
                    }
                done_payload: dict[str, object] = {
                    "type": "done",
                    "output": result.output,
                    "turns": len(conv.turns),
                    "conversation_id": conv.id,
                    "events": events,
                    "usage": usage_data,
                }
                if result.trace_data:
                    done_payload["trace"] = result.trace_data
                yield f"data: {json.dumps(done_payload)}\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
