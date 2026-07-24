"""The engine-agnostic pipeline **drive contract** (design/details/orchestration-provider-seam.md).

Pipeline *sequencing* is a pluggable seam, not a bespoke saga engine SwarmKit owns. This module
holds the *only* part of that seam the runtime carries: the small, generic **drive contract** an
orchestrator calls into — "run this stage as a bounded governed run" — plus the shape of what a
stage run returns. It is deliberately **domain-neutral**: the runtime models no business instance,
it only stamps an opaque ``correlation_id`` on each run so the append-only audit assembles the
cross-stage trail regardless of what sequenced it.

What lives elsewhere, on purpose:

- The ``OrchestrationProvider`` a durable engine *implements* (start / signal / state / cancel), the
  live ``SagaView``, and any domain vocabulary (a requirement id, a contract lock) belong to the
  orchestrator — see ``examples/sdlc-pipeline/orchestrator/`` for the reference controller and the
  Temporal adapter. The runtime never imports them.

The two runtime-side seams (design "What SwarmKit exposes"):

- :data:`RunStage` — ``(correlation_id, stage) -> StageOutcome``: kick the stage's topology as a
  bounded run, stamped with the correlation id. Production wraps ``swarmkit serve``; tests/demos
  wrap the ``StageRunner`` or a scripted stub.
- the gate-resolution notification (an orchestrator *learns* a funnel gate's result; the pause
  itself is a SwarmKit checkpoint resumed by humans) — surfaced over serve, not a type here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

# Terminal outcome of one bounded stage run. `parked` = the stage produced its artifact and is
# waiting on its funnel gate (resolved out of band, learned via the gate-status seam); `completed`
# = a clean, ungated finish; `rejected` = the gate rejected; `denied` = IAM scope denied the stage;
# `failed` = the run errored. Domain-neutral — no business status leaks in.
StageStatus = Literal["completed", "parked", "rejected", "denied", "failed"]


@dataclass(frozen=True)
class StageOutcome:
    """What a stage run returns to whatever is sequencing it."""

    status: StageStatus
    artifact: str = ""
    detail: str = ""


# The drive seam: run a stage's topology as a bounded, governed SwarmKit run. The first argument is
# the opaque ``correlation_id`` (the audit-correlation contract); the second is the stage spec.
# Injected by the deployment — never a business type, always this generic shape.
RunStage = Callable[[str, dict[str, Any]], Awaitable[StageOutcome]]


# The ingress signal sink: deliver one structured pipeline event to whatever is sequencing the saga
# (the reference controller, Temporal — behind the ``OrchestrationProvider`` seam). Both arguments
# are opaque strings — ``(correlation_id, event)`` — so the runtime carries no business vocabulary
# and no sequencing state. Dedup, start/resume/skip routing, and ordering are the orchestrator's
# job; the runtime only authorizes, audits, and hands off. Injected by the deployment
# (``app.state.pipeline_signal``), exactly like :data:`RunStage` — never a business type.
PipelineSignal = Callable[[str, str], Awaitable[None]]


__all__ = [
    "PipelineSignal",
    "RunStage",
    "StageOutcome",
    "StageStatus",
]
