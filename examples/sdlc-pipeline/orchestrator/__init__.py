"""Reference orchestrator — a generic, domain-neutral implementation of the pipeline
sequencing seam (design/details/orchestration-provider-seam.md).

Pipeline *sequencing* is a pluggable seam, not a bespoke saga engine. The runtime keeps the
StageGraph spec, the governed stage run, and the correlated audit; an orchestrator owns
state / timers / signals / locking / compensation and drives the runtime's **generic drive
contract** — :class:`StageOutcome`, :data:`StageStatus`, :data:`RunStage`
(``(correlation_id, stage) -> StageOutcome``), imported from ``swarmkit_runtime.orchestration``.

Everything here is **domain-neutral**: an orchestrator is keyed only on an opaque
``correlation_id`` and interprets any StageGraph — it models no business instance (no
"requirement", "order", or "ticket"). This is a reusable reference the SDLC workspace
consumes concretely (wiring these to its OMS StageGraph); it is not SDLC-specific.

- :class:`OrchestrationProvider` — the interface (start / signal / state / cancel).
- :class:`SagaView` — the live status of one pipeline run.
- the in-memory ``controller`` — the zero-infra reference adapter.
- ``temporal`` — the production adapter (Temporal; the selected engine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from swarmkit_runtime.orchestration import RunStage, StageOutcome, StageStatus


@dataclass
class SagaView:
    """Live status of one pipeline run (keyed on an opaque correlation_id)."""

    correlation_id: str
    status: str = "active"  # active | parked | done | rejected | cancelled | failed
    current_stage: str | None = None
    passed_stages: list[str] = field(default_factory=list)
    pending_gate: str | None = None


class OrchestrationProvider(Protocol):
    """Drives a StageGraph as a durable saga. Implementations: the in-memory reference controller
    (zero-infra) and the Temporal adapter (production)."""

    async def start(
        self, correlation_id: str, graph: dict[str, Any], initial_event: str
    ) -> None: ...

    async def signal_event(self, correlation_id: str, event: str) -> None: ...

    async def resolve_gate(self, correlation_id: str, gate: str, *, approved: bool) -> None: ...

    async def cancel(self, correlation_id: str) -> None: ...

    async def state(self, correlation_id: str) -> SagaView: ...


__all__ = [
    "OrchestrationProvider",
    "RunStage",
    "SagaView",
    "StageOutcome",
    "StageStatus",
]
