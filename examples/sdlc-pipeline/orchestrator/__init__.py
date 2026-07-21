"""Orchestration provider seam (design/details/orchestration-provider-seam.md).

Pipeline *sequencing* is a pluggable seam, not a bespoke saga engine. SwarmKit keeps the
StageGraph spec, the governed stage run, and the correlated audit; a durable-workflow engine
owns state / timers / signals / locking / compensation. This package defines the seam and its
adapters:

- :class:`OrchestrationProvider` — the interface (start / signal / state / cancel).
- ``temporal`` — the production adapter (Temporal; the selected engine).
- the slice-5 in-memory controller is the zero-infra reference adapter.

The ``run_stage`` seam a provider drives is ``(requirement_id, stage) -> StageOutcome`` — the
slice-4 StageRunner behind a stable call, stamped with ``correlation_id = requirement_id``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# The drive seam: run a stage's topology as a bounded, governed SwarmKit run.
# `parked` = the stage produced its artifact and is waiting on its funnel gate (resolved out of
# band and delivered to the provider as a gate signal); `completed` = clean, ungated finish.
StageStatus = Literal["completed", "parked", "rejected", "denied", "failed"]


@dataclass(frozen=True)
class StageOutcome:
    status: StageStatus
    artifact: str = ""
    detail: str = ""


# Injected by the deployment: production wraps `swarmkit serve`; tests/demo wrap the StageRunner
# or a scripted stub. Always stamped with the requirement id (the correlation contract).
RunStage = Callable[[str, dict[str, Any]], Awaitable[StageOutcome]]


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
