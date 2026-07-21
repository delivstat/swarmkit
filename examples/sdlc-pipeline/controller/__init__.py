"""SDLC pipeline controller — a reference saga-sequencing component (SwarmKit slice 5).

A self-contained service that owns durable per-requirement saga state and drives bounded
SwarmKit stage runs over an injected ``run_stage`` seam. It is **not** a SwarmKit runtime feature
and **not** an agent — it is the "application" half of the Minder split
(``feedback_llm_language_code_doing``): the app owns logic + state; SwarmKit does bounded
determination + governance. A different pipeline is new *data* (a stage-graph artifact), not new
controller code. See design/details/pipeline-controller.md.
"""

from __future__ import annotations

from ._controller import DEFAULT_MAX_ATTEMPTS, PipelineController
from ._events import (
    CloseGateCallback,
    InboundEvent,
    RunStage,
    SourceStateProvider,
    StageRunOutcome,
    StageRunRequest,
    StageRunStatus,
    SurfaceCallback,
    SurfaceNotice,
)
from ._graph import Loop, Route, Stage, StageGraph
from ._locks import LockManager
from ._saga import (
    InMemorySagaStore,
    SagaState,
    SagaStatus,
    SagaStore,
    TimelineEntry,
)

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "CloseGateCallback",
    "InMemorySagaStore",
    "InboundEvent",
    "LockManager",
    "Loop",
    "PipelineController",
    "Route",
    "RunStage",
    "SagaState",
    "SagaStatus",
    "SagaStore",
    "SourceStateProvider",
    "Stage",
    "StageGraph",
    "StageRunOutcome",
    "StageRunRequest",
    "StageRunStatus",
    "SurfaceCallback",
    "SurfaceNotice",
    "TimelineEntry",
]
