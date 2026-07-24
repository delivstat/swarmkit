"""Orchestration provider seam (design/details/orchestration-provider-seam.md).

Pipeline *sequencing* is a pluggable seam, not a bespoke saga engine. SwarmKit keeps the
StageGraph spec, the governed stage run, and the correlated audit; a durable-workflow engine
owns state / timers / signals / locking / compensation.

The **generic drive contract** the runtime exposes — :class:`StageOutcome`, :data:`StageStatus`,
:data:`RunStage` (``(correlation_id, stage) -> StageOutcome``) — is imported from the runtime
(``swarmkit_runtime.orchestration``); it is domain-neutral and stamped only with an opaque
``correlation_id``.

What an orchestrator *implements* lives here in the example, where the SDLC domain (a
``requirement_id``) is allowed:

- :class:`OrchestrationProvider` — the interface (start / signal / state / cancel).
- :class:`SagaView` — the live status of one requirement's pipeline run.
- ``temporal`` — the production adapter (Temporal; the selected engine).
- the slice-5 in-memory controller is the zero-infra reference adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from swarmkit_runtime.orchestration import RunStage, StageOutcome, StageStatus


@dataclass
class SagaView:
    """Live status of one requirement's pipeline run."""

    requirement_id: str
    status: str = "active"  # active | parked | done | rejected | cancelled | failed
    current_stage: str | None = None
    passed_stages: list[str] = field(default_factory=list)
    pending_gate: str | None = None


class OrchestrationProvider(Protocol):
    """Drives a StageGraph as a durable saga. Implementations: the in-memory reference controller
    (zero-infra) and the Temporal adapter (production)."""

    async def start(
        self, requirement_id: str, graph: dict[str, Any], initial_event: str
    ) -> None: ...

    async def signal_event(self, requirement_id: str, event: str) -> None: ...

    async def resolve_gate(self, requirement_id: str, gate: str, *, approved: bool) -> None: ...

    async def cancel(self, requirement_id: str) -> None: ...

    async def state(self, requirement_id: str) -> SagaView: ...


__all__ = [
    "OrchestrationProvider",
    "RunStage",
    "SagaView",
    "StageOutcome",
    "StageStatus",
]
