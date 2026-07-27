"""The bundled reference pipeline orchestrator (design/details/bundled-pipeline-orchestrator.md).

A durable, domain-neutral saga controller — the default engine behind the `swarmkit orchestrator`
command. Imported ONLY by that command; never by the runtime core or serve (import-linter enforced).
"""

from __future__ import annotations

from swarmkit_runtime.orchestration.reference._controller import ReferenceController
from swarmkit_runtime.orchestration.reference._saga import (
    InMemorySagaStore,
    SagaState,
    SagaStatus,
    SagaStore,
    TimelineEntry,
)
from swarmkit_runtime.orchestration.reference._store import SqlSagaStore

__all__ = [
    "InMemorySagaStore",
    "ReferenceController",
    "SagaState",
    "SagaStatus",
    "SagaStore",
    "SqlSagaStore",
    "TimelineEntry",
]
