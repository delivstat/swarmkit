"""The inbound event front door: `POST /events/signal`.

Was `POST /pipelines/signal`, and moved here when the bundled sequencer was removed
(`docs/notes/pipeline-deprecation.md`). The route survives because it is not sequencing — it turns
an authorised outside event into a structured ``(correlation_id, event)`` and hands it to whatever
sink the deployment configured. That is how an application-owned orchestrator is DRIVEN.

The guardrail is the reason it is worth keeping intact: ``advance`` and ``skip`` are operator acts
requiring a reserved human-identity scope through the GovernanceProvider, never a transport-token
capability; every attempt is audited allowed or denied; and delivery is a 503 when no sink is
configured rather than a silent success.

With no bundled sequencer, nothing sets a sink by default — an application assigns
``app.state.pipeline_signal``. A 503 here means "nobody is listening", which is the honest answer.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from swarmkit_runtime.triggers import (
    PipelineIngressError,
    PipelineMode,
    PipelineSignal,
    _ingress_pipeline_event,
)

from ._helpers import _get_runtime


class PipelineSignalRequest(BaseModel):
    """One structured pipeline event, submitted through the ingress front door.

    Domain-neutral: ``correlation_id`` is an opaque handle (never a business id), ``event`` is the
    structured event to signal, ``source_event_id`` is passed through for the orchestrator's dedup
    (the runtime keeps no dedup state), and ``mode`` selects the guardrail — ``emit`` (default) is
    an ordinary authorised event; ``advance`` / ``skip`` are operator acts gated on a reserved
    human-identity scope.
    """

    correlation_id: str
    event: str
    source_event_id: str | None = None
    mode: PipelineMode = "emit"


class PipelineSignalResponse(BaseModel):
    """Acknowledgement that an authorised event was audited and delivered to the signal seam."""

    delivered: bool
    correlation_id: str
    event: str
    mode: PipelineMode
    source: str


def _register_event_routes(app: FastAPI) -> None:
    """Register the inbound event ingress."""

    @app.post("/events/signal")
    async def signal_event(body: PipelineSignalRequest, request: Request) -> PipelineSignalResponse:
        runtime = _get_runtime(request)
        identity = getattr(request.state, "identity", None)
        actor = getattr(identity, "client_id", None) or "anonymous"
        source = f"api:{actor}"
        seam: PipelineSignal | None = getattr(request.app.state, "pipeline_signal", None)
        try:
            await _ingress_pipeline_event(
                governance=runtime.governance,
                signal=seam,
                correlation_id=body.correlation_id,
                event=body.event,
                mode=body.mode,
                actor_identity=actor,
                source=source,
                source_event_id=body.source_event_id,
            )
        except PipelineIngressError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return PipelineSignalResponse(
            delivered=True,
            correlation_id=body.correlation_id,
            event=body.event,
            mode=body.mode,
            source=source,
        )
