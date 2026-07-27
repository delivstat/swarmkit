"""Comprehension-debt telemetry — signals derived from the append-only audit log (read-only).

Slice 3 of ``design/details/gate-coverage-and-comprehension-debt.md``. **Report-only**: never a
gate, never a score — a list of edges/approvals a human should look at. The one signal fully
derivable from today's audit log is **fast-approve**: a human approval resolved implausibly fast
after its gate opened (``approval.gate_opened`` → ``approval.gate_resolved``, paired by
``(run_id, gate_id)``). The note's other signals need data the audit does not yet carry; they are
disclosed as :data:`DEFERRED_SIGNALS` with the reason rather than faked (the note's discipline).

Both ``swarmkit comprehension`` and ``GET /comprehension`` call :func:`compute_comprehension`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from swarmkit_runtime.governance import AuditEvent

#: A human resolving a gate faster than this many seconds after it opened is implausible-to-read.
#: A heuristic (the audit lacks artifact size for a size-scaled estimate) — hence report-only.
DEFAULT_FAST_APPROVE_SECONDS = 20.0

#: Signals from the note not yet derivable from the audit log — disclosed, never faked.
DEFERRED_SIGNALS: tuple[str, ...] = (
    "oversized-slice — needs the produced artifact/diff size in the audit payload",
    "uncited-change — needs the change-rationale artifact (slice 5)",
    "stale-audit — needs the recurring expert audit (slice 6)",
)


@dataclass(frozen=True)
class FastApprove:
    """A human approval resolved suspiciously fast after its gate opened."""

    gate_id: str
    run_id: str | None
    latency_seconds: float
    distinct_approvers: int
    timestamp: datetime


@dataclass(frozen=True)
class ComprehensionReport:
    """Read-only comprehension signals over an audited window. Not a score."""

    fast_approvals: tuple[FastApprove, ...]
    approvals_seen: int
    threshold_seconds: float
    deferred: tuple[str, ...] = field(default=DEFERRED_SIGNALS)

    def verdict(self) -> str:
        if self.approvals_seen == 0:
            return "no human approvals in the audited window — nothing to assess."
        n = len(self.fast_approvals)
        if n == 0:
            return (
                f"{self.approvals_seen} approval(s), none resolved faster than "
                f"{self.threshold_seconds:g}s."
            )
        return (
            f"{n} of {self.approvals_seen} approval(s) resolved under {self.threshold_seconds:g}s "
            "— possible rubber-stamp; look at the artifacts."
        )


def compute_comprehension(
    events: Iterable[AuditEvent],
    *,
    fast_approve_threshold_seconds: float = DEFAULT_FAST_APPROVE_SECONDS,
) -> ComprehensionReport:
    """Derive comprehension signals from audit events. Pure + read-only."""
    ordered = sorted(events, key=lambda e: e.timestamp)
    opened: dict[tuple[str | None, str], datetime] = {}
    fast: list[FastApprove] = []
    approvals = 0

    for e in ordered:
        if e.event_type == "approval.gate_opened":
            opened[(e.run_id, str(e.payload.get("gate_id", "")))] = e.timestamp
        elif e.event_type == "approval.gate_resolved":
            if str(e.payload.get("status", "")) != "approved":
                continue  # rejections / timeouts are not rubber-stamps
            approvals += 1
            gid = str(e.payload.get("gate_id", ""))
            opened_at = opened.get((e.run_id, gid))
            if opened_at is None:
                continue  # no paired open event in this window
            latency = (e.timestamp - opened_at).total_seconds()
            if latency < fast_approve_threshold_seconds:
                approvers = e.payload.get("approvers")
                n_appr = len(approvers) if isinstance(approvers, list) else 0
                fast.append(FastApprove(gid, e.run_id, latency, n_appr, e.timestamp))

    return ComprehensionReport(tuple(fast), approvals, fast_approve_threshold_seconds)


def comprehension_to_dict(report: ComprehensionReport) -> dict[str, object]:
    """JSON-serializable report — the shared shape behind the CLI ``--json`` and the endpoint."""
    return {
        "verdict": report.verdict(),
        "threshold_seconds": report.threshold_seconds,
        "approvals_seen": report.approvals_seen,
        "fast_approvals": [
            {
                "gate_id": f.gate_id,
                "run_id": f.run_id,
                "latency_seconds": round(f.latency_seconds, 3),
                "distinct_approvers": f.distinct_approvers,
                "timestamp": f.timestamp.isoformat(),
            }
            for f in report.fast_approvals
        ],
        "deferred": list(report.deferred),
    }


__all__ = [
    "DEFAULT_FAST_APPROVE_SECONDS",
    "DEFERRED_SIGNALS",
    "ComprehensionReport",
    "FastApprove",
    "comprehension_to_dict",
    "compute_comprehension",
]
