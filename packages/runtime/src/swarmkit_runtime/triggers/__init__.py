"""Triggers — scheduled and inbound events that start work.

Also home to the inbound EVENT SEAM. It lived in `orchestration` and left with it, but a webhook or
an MCP tool emitting a correlated event is not part of a sequencer: it is how an
application-owned orchestrator is DRIVEN (`docs/notes/pipeline-deprecation.md`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from swarmkit_runtime.triggers._ingress import (
    PipelineIngressError,
    PipelineMode,
    _ingress_pipeline_event,
)
from swarmkit_runtime.triggers._pipeline_ingress import (
    extract_correlation_id,
    find_pipeline_webhook_trigger,
)
from swarmkit_runtime.triggers._scheduler import TriggerScheduler

#: Deliver a correlated external event to whatever is listening: ``(correlation_id, event_name)``.
#: A type alias, not an engine — callers depend on the shape, and nothing here knows what consumes
#: it.
EventSignal = Callable[[str, str], Awaitable[None]]

#: The name it carried while the bundled sequencer was its only consumer.
PipelineSignal = EventSignal

__all__ = [
    "EventSignal",
    "PipelineIngressError",
    "PipelineMode",
    "PipelineSignal",
    "TriggerScheduler",
    "_ingress_pipeline_event",
    "extract_correlation_id",
    "find_pipeline_webhook_trigger",
]
