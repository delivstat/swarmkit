"""Event model, the stage-run seam, and surface notices for the pipeline controller.

These are the value objects that cross the controller's boundaries: inbound events (external
webhooks + prior-stage signals), the ``run_stage`` seam contract (the controller drives bounded
SwarmKit stage runs *only* through this callable — it never embeds the runtime), and the notice
raised when something must surface to a human. See design/details/pipeline-controller.md.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

# The status a stage run reports back over the seam.
#   completed  — clean finish; the controller emits the stage's `success` signal.
#   parked     — the run parked on its gate; the controller waits for `resolve_gate`.
#   rejected   — the gate rejected the artifact (a terminal stage outcome).
#   denied     — an IAM scope denied the stage (a terminal stage outcome).
#   failed     — the run crashed / a dependency was unreachable; retried idempotently.
StageRunStatus = Literal["completed", "parked", "rejected", "denied", "failed"]


@dataclass(frozen=True)
class InboundEvent:
    """An event the controller reacts to — an external webhook or a prior-stage signal.

    The idempotency key is ``(requirement_id, event, source_event_id)`` (design
    "Event model + idempotency"): external webhooks duplicate, arrive out of order, and go
    missing, so a single delivery is never trusted. A repeated key is a no-op.
    """

    requirement_id: str
    event: str
    source_event_id: str
    payload: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.requirement_id, self.event, self.source_event_id)


@dataclass(frozen=True)
class StageRunRequest:
    """What the controller hands the seam to kick one bounded stage run.

    ``requirement_id`` is the correlation id stamped on the run + its audit so the append-only
    audit can be assembled across per-stage runs (design "Run correlation label").
    """

    requirement_id: str
    stage_id: str
    topology: str
    gate: str | None
    payload: str
    attempt: int = 1
    is_compensation: bool = False


@dataclass(frozen=True)
class StageRunOutcome:
    """What the seam reports back for one stage run."""

    status: StageRunStatus
    detail: str = ""


@dataclass(frozen=True)
class SurfaceNotice:
    """Raised to the human-surface callback when a stage cannot proceed on its own.

    A wait (gate / lock) is cheap persisted state and is *not* surfaced; this is for the cases
    the design says must never be silently dropped: a repeatedly-failing stage run, an IAM
    denial, a gate rejection, or a failed compensation.
    """

    requirement_id: str
    stage_id: str
    reason: str
    detail: str = ""


# The seam. In the demo/tests this wraps StageRunner or a scripted stub; in production it is a
# `swarmkit serve` HTTP call. The controller depends on this callable, never on the runtime.
RunStage = Callable[[StageRunRequest], Awaitable[StageRunOutcome]]

# Mock source-of-truth for reconciliation: given a requirement id, return the set of event
# names the source systems (Jira/CI/Git/SAST) currently show as having occurred. In production
# this polls those systems; in the demo/tests it is a scripted stub.
SourceStateProvider = Callable[[str], "set[str]"]

# Human-surface + gate-close callbacks (the board / notification edges live elsewhere).
SurfaceCallback = Callable[[SurfaceNotice], None]
CloseGateCallback = Callable[[str, str], None]


__all__ = [
    "CloseGateCallback",
    "InboundEvent",
    "RunStage",
    "SourceStateProvider",
    "StageRunOutcome",
    "StageRunRequest",
    "StageRunStatus",
    "SurfaceCallback",
    "SurfaceNotice",
]
