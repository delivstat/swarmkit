"""`POST /api/ag-ui/run` — AG-UI protocol surface for frontends.

See `design/details/ag-ui-protocol.md` + `docs/notes/ag-ui-integration.md`. v1 implements the
subset of AG-UI's event vocabulary that can be faithfully produced from SwarmKit's existing
string-based progress stream: Lifecycle (`RunStarted`, `RunFinished`, `RunError`) + Messages
(`TextMessageStart`/`Content`/`End`). The richer events (tool calls, subagents, interrupts)
need a structured event bus that lands in a follow-up — see the handoff guide's "Gotchas"
section.

Governance invariant: an AG-UI run and a `/run/{topology}` run produce byte-identical audit
output. We call the same `JobService.start()`; the only difference is the shape of the
response stream. Nothing bespoke runs here.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ._helpers import _get_runtime
from ._jobs import JobStore
from ._routes_jobs import _app_state_run_deps
from ._services import JobService, ServiceError

#: Error codes used in `RunError.code`. See `docs/notes/ag-ui-integration.md` §5.
ERR_INTERNAL = "internal"


class _Message(BaseModel):
    """One AG-UI message in `RunAgentInput.messages`. Open shape — the client picks the role
    vocabulary. v1 reads only the trailing user turn; see handoff guide §9 Gotcha 2."""

    role: str
    content: str = ""


class RunAgentInput(BaseModel):
    """AG-UI's canonical input to a run. SwarmKit extension: `context.topology` names the
    topology to execute. See `design/details/ag-ui-protocol.md` §Endpoint shape."""

    threadId: str | None = None
    runId: str | None = None
    messages: list[_Message] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ulid_like() -> str:
    """ULID-shaped id without the dependency — monotonic prefix + random suffix is enough for a
    thread/run identifier."""
    return f"{_now_ms():x}-{uuid.uuid4().hex[:12]}"


def _event(payload: dict[str, Any]) -> str:
    """Serialize one AG-UI event into an SSE `data:` frame."""
    return f"data: {json.dumps(payload)}\n\n"


def _register_ag_ui_routes(app: FastAPI, job_store: JobStore) -> None:
    jobs = JobService(job_store)

    @app.post("/api/ag-ui/run")
    async def ag_ui_run(body: RunAgentInput, request: Request) -> StreamingResponse:
        """Run a topology, streaming AG-UI events over SSE.

        Topology is picked from `context.topology` (AG-UI's `context` is open-key, so this is a
        namespaced extension — no protocol departure). v1 translates SwarmKit's string progress
        stream into AG-UI's Lifecycle + Messages events. Richer events (tool calls, subagents,
        interrupts) land in a follow-up when the structured event bus ships.
        """
        topology_name = str(body.context.get("topology", "")).strip()
        if not topology_name:
            raise HTTPException(400, "context.topology is required")

        rt = _get_runtime(request)
        if topology_name not in rt.workspace.topologies:
            raise HTTPException(404, f"topology_not_found: {topology_name}")

        thread_id = (body.threadId or _ulid_like()).strip() or _ulid_like()
        # v1 reads only the trailing user turn; the rest is logged but not forwarded. See the
        # handoff guide's Gotcha 2 for the rationale.
        last_user = next(
            (m.content for m in reversed(body.messages) if m.role == "user" and m.content),
            "",
        )

        canary, store, cfg, semaphore = _app_state_run_deps(request)

        try:
            job = await jobs.start(
                rt=rt,
                canary=canary,
                store=store,
                cfg=cfg,
                semaphore=semaphore,
                topology_name=topology_name,
                user_input=last_user,
                max_steps=10,
                correlation_id=thread_id,
                labels={"ag_ui.thread_id": thread_id},
                parent_job_id=None,
                attachments=[],
                enqueue_only=getattr(request.app.state, "enqueue_only", False),
                queue_max_depth=getattr(request.app.state, "queue_max_depth", 0),
            )
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

        run_id = (body.runId or job.id).strip() or job.id
        return StreamingResponse(
            _translate(job, thread_id=thread_id, run_id=run_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )


async def _translate(job: Any, *, thread_id: str, run_id: str) -> AsyncGenerator[str]:
    """Translate a job's string progress stream into AG-UI events.

    Shape of what we emit:
        RunStarted
        TextMessageStart
        TextMessageContent  * one per progress line
        TextMessageEnd
        RunFinished{success}   OR   RunError

    Richer events (tool calls, subagents, interrupts) are v2 — see handoff guide §9 Gotcha 1.
    """
    message_id = f"msg-{uuid.uuid4().hex[:12]}"

    yield _event(
        {
            "type": "RunStarted",
            "timestamp": _now_ms(),
            "runId": run_id,
            "threadId": thread_id,
            "input": {"topology": job.topology},
        }
    )
    yield _event(
        {
            "type": "TextMessageStart",
            "timestamp": _now_ms(),
            "messageId": message_id,
            "role": "assistant",
        }
    )

    sent = 0
    while True:
        current_events = job.events[sent:]
        for event in current_events:
            yield _event(
                {
                    "type": "TextMessageContent",
                    "timestamp": _now_ms(),
                    "messageId": message_id,
                    "delta": event,
                }
            )
            sent += 1
        if job.status in ("completed", "failed"):
            break
        await asyncio.sleep(0.3)

    yield _event(
        {
            "type": "TextMessageEnd",
            "timestamp": _now_ms(),
            "messageId": message_id,
        }
    )

    if job.status == "completed":
        yield _event(
            {
                "type": "RunFinished",
                "timestamp": _now_ms(),
                "runId": run_id,
                "outcome": {"type": "success"},
                "result": {"output": getattr(job, "output", None) or ""},
            }
        )
    else:
        yield _event(
            {
                "type": "RunError",
                "timestamp": _now_ms(),
                "runId": run_id,
                "message": getattr(job, "error", None) or "run failed",
                "code": ERR_INTERNAL,
            }
        )
