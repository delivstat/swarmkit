"""Requesting that a run stop (design/details/stopping-a-run.md).

`swarmkit stop` and `POST /jobs/{id}/stop` are two front doors onto **one** act, so the flag write
and the audit record live here rather than in each of them. Two implementations of "stop a run"
would drift exactly where it matters: one of them would forget to record who did it.

A stop is a human act against a governed run. "Who stopped the release run" is precisely the kind of
question the audit log exists for, and a stop that appeared only as a status change could not answer
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StopOutcome:
    """What asking to stop did. ``requested`` is False when there was nothing to stop."""

    run_id: str
    status: str
    requested_at: str = ""
    already_requested: bool = False

    @property
    def requested(self) -> bool:
        return bool(self.requested_at)


#: A run can only be stopped while it might still run something.
STOPPABLE: frozenset[str] = frozenset({"pending", "running"})


def request_stop(store: Any, run_id: str, *, requested_by: str = "") -> StopOutcome | None:
    """Flag a run to stop at its next agent boundary. None when the run id is unknown.

    Idempotent: asking twice re-reports the pending request rather than stacking, because an
    operator who cannot tell whether the first one landed will always press it again.
    """
    row = store.get_job(run_id)
    if row is None:
        return None
    status = str(row.status)
    if status not in STOPPABLE:
        return StopOutcome(run_id=run_id, status=status)
    existing = getattr(row, "stop_requested_at", None)
    if existing:
        return StopOutcome(
            run_id=run_id, status=status, requested_at=str(existing), already_requested=True
        )
    now = datetime.now(UTC).isoformat()
    store.update_job(run_id, stop_requested_at=now)
    return StopOutcome(run_id=run_id, status=status, requested_at=now)


async def record_stop_requested(
    workspace_root: Path | str, outcome: StopOutcome, *, requested_by: str, topology_id: str = ""
) -> None:
    """Append `run.stopped` to the audit trail.

    Best-effort: an audit store that will not open must not prevent a human from stopping a run.
    The flag is already written by then, and losing the record is the lesser failure — but it is a
    real one, so it is not silently swallowed anywhere the caller could have reported it.
    """
    if not outcome.requested or outcome.already_requested:
        return
    from swarmkit_runtime.audit import audit_provider_for_path  # noqa: PLC0415
    from swarmkit_runtime.governance import AuditEvent  # noqa: PLC0415

    provider = audit_provider_for_path(Path(workspace_root))
    await provider.record(
        AuditEvent(
            event_type="run.stopped",
            agent_id="human",
            timestamp=datetime.now(UTC),
            run_id=outcome.run_id,
            topology_id=topology_id,
            payload={"requested_by": requested_by or "unknown", "at": outcome.requested_at},
        )
    )


__all__ = ["STOPPABLE", "StopOutcome", "record_stop_requested", "request_stop"]
