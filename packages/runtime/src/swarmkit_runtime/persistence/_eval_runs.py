"""Eval-run rows — the durable record of one eval run (design/details/eval-on-serve.md §3, §4).

A mixin on :class:`Store`, so an eval run lives wherever the workspace's runtime store does (SQLite
or Postgres) and every front door reads the same history. Each write is one short transaction:
the row is the eval's checkpoint, so a case result is durable the moment it is recorded and a
resume never re-runs or re-charges it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, RowMapping, select, update

from swarmkit_runtime.persistence._tables import eval_runs

#: The statuses a run finishes in. `interrupted` is not one: it is what the sweep says about a run
#: whose process died, and it can be resumed.
FINISHED_EVAL_STATUSES = ("succeeded", "failed", "stopped", "error")
#: The statuses a resume may continue from.
RESUMABLE_EVAL_STATUSES = ("interrupted", "stopped")


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class EvalRunRow:
    """A persisted eval run."""

    id: str
    eval_set_id: str
    target: str
    status: str
    source: str
    #: the eval set as it stood when the run started (a JSON-shaped dict)
    eval_set: dict[str, Any]
    #: EvalReport.to_dict() — the cases recorded so far
    report: dict[str, Any] = field(default_factory=dict)
    #: ReportDiff fields against the previous finished run, or None when there was none
    diff: dict[str, Any] | None = None
    passed: int = 0
    total: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    started_at: str = ""
    heartbeat_at: str = ""
    finished_at: str | None = None
    stop_requested_at: str | None = None

    @property
    def cases(self) -> list[dict[str, Any]]:
        raw = self.report.get("cases", [])
        return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []

    @property
    def recorded_case_ids(self) -> set[str]:
        return {str(c.get("case_id")) for c in self.cases}


class EvalRunStoreMixin:
    """`eval_runs` reads and writes. Mixed into :class:`Store`, which provides ``_engine``."""

    _engine: Engine

    def create_eval_run(
        self,
        run_id: str,
        *,
        eval_set_id: str,
        target: str,
        source: str,
        eval_set: dict[str, Any],
        total: int,
    ) -> EvalRunRow:
        now = _now()
        report = {"eval_set_id": eval_set_id, "target": target, "started_at": now, "cases": []}
        with self._engine.begin() as conn:
            conn.execute(
                eval_runs.insert().values(
                    id=run_id,
                    eval_set_id=eval_set_id,
                    target=target,
                    status="running",
                    source=source,
                    eval_set=json.dumps(eval_set),
                    report=json.dumps(report),
                    passed=0,
                    total=total,
                    cost_usd=0.0,
                    started_at=now,
                    heartbeat_at=now,
                )
            )
        row = self.get_eval_run(run_id)
        assert row is not None
        return row

    def record_eval_case(self, run_id: str, case: dict[str, Any], cost_usd: float = 0.0) -> None:
        """Append one case's result, add its cost, and move the heartbeat — atomically.

        One writer per run (the process executing it), so a read-modify-write inside a single
        transaction is enough; there is no concurrent appender to lose an update to.
        """
        with self._engine.begin() as conn:
            row = conn.execute(
                select(eval_runs.c.report, eval_runs.c.passed, eval_runs.c.cost_usd).where(
                    eval_runs.c.id == run_id
                )
            ).first()
            if row is None:
                return
            report = json.loads(row.report) if row.report else {"cases": []}
            report.setdefault("cases", []).append(case)
            conn.execute(
                update(eval_runs)
                .where(eval_runs.c.id == run_id)
                .values(
                    report=json.dumps(report),
                    passed=int(row.passed or 0) + (1 if case.get("passed") else 0),
                    cost_usd=float(row.cost_usd or 0.0) + float(cost_usd),
                    heartbeat_at=_now(),
                )
            )

    def touch_eval_run(self, run_id: str) -> None:
        """Move the heartbeat without recording anything — the run is alive mid-case."""
        with self._engine.begin() as conn:
            conn.execute(
                update(eval_runs)
                .where(eval_runs.c.id == run_id, eval_runs.c.status == "running")
                .values(heartbeat_at=_now())
            )

    def finish_eval_run(
        self,
        run_id: str,
        status: str,
        *,
        report: dict[str, Any] | None = None,
        diff: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "finished_at": _now(), "heartbeat_at": _now()}
        if report is not None:
            values["report"] = json.dumps(report)
            values["passed"] = int(report.get("passed", 0) or 0)
        if diff is not None:
            values["diff"] = json.dumps(diff)
        if error is not None:
            values["error"] = error
        with self._engine.begin() as conn:
            conn.execute(update(eval_runs).where(eval_runs.c.id == run_id).values(**values))

    def claim_eval_resume(self, run_id: str) -> bool:
        """Move an interrupted or stopped run back to `running`. False if it is in any other state.

        Conditional on the current status in one UPDATE, so two callers resuming the same run can't
        both win and run its remaining cases twice.
        """
        now = _now()
        stmt = (
            update(eval_runs)
            .where(eval_runs.c.id == run_id, eval_runs.c.status.in_(RESUMABLE_EVAL_STATUSES))
            .values(
                status="running",
                heartbeat_at=now,
                finished_at=None,
                error=None,
                stop_requested_at=None,
            )
        )
        with self._engine.begin() as conn:
            return int(conn.execute(stmt).rowcount or 0) == 1

    def request_eval_stop(self, run_id: str) -> bool:
        """Ask a running eval to stop after its current case. False if it isn't running."""
        stmt = (
            update(eval_runs)
            .where(eval_runs.c.id == run_id, eval_runs.c.status == "running")
            .values(stop_requested_at=_now())
        )
        with self._engine.begin() as conn:
            return int(conn.execute(stmt).rowcount or 0) == 1

    def eval_stop_requested(self, run_id: str) -> bool:
        with self._engine.connect() as conn:
            value = conn.execute(
                select(eval_runs.c.stop_requested_at).where(eval_runs.c.id == run_id)
            ).scalar()
        return value is not None

    def get_eval_run(self, run_id: str) -> EvalRunRow | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(eval_runs).where(eval_runs.c.id == run_id)).mappings().first()
        return _row_to_eval_run(row) if row is not None else None

    def list_eval_runs(
        self, eval_set_id: str | None = None, *, limit: int = 20
    ) -> list[EvalRunRow]:
        """Eval runs, newest first; ``eval_set_id`` narrows to one set."""
        stmt = select(eval_runs)
        if eval_set_id is not None:
            stmt = stmt.where(eval_runs.c.eval_set_id == eval_set_id)
        stmt = stmt.order_by(eval_runs.c.started_at.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [_row_to_eval_run(r) for r in rows]

    def latest_finished_eval_run(
        self,
        eval_set_id: str,
        *,
        exclude_id: str | None = None,
        finished_before: str | None = None,
    ) -> EvalRunRow | None:
        """The newest run of a set that finished with a report (`succeeded` or `failed`).

        Never a run still in flight, and never a stopped or errored one — a diff against a partial
        report would call every unrecorded case "new". ``finished_before`` keeps a run that
        finished while this one was in flight from counting as its prior.
        """
        stmt = select(eval_runs).where(
            eval_runs.c.eval_set_id == eval_set_id,
            eval_runs.c.status.in_(("succeeded", "failed")),
        )
        if exclude_id is not None:
            stmt = stmt.where(eval_runs.c.id != exclude_id)
        if finished_before is not None:
            stmt = stmt.where(eval_runs.c.finished_at < finished_before)
        stmt = stmt.order_by(eval_runs.c.finished_at.desc()).limit(1)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return _row_to_eval_run(row) if row is not None else None

    def sweep_stale_eval_runs(self, older_than_seconds: float, reason: str) -> int:
        """Mark runs whose heartbeat is older than ``older_than_seconds`` as `interrupted`.

        Bounded by the heartbeat, not by "everything running at startup": several instances (and
        CLI processes) can share one store, and a blanket sweep would close another live process's
        eval. A live run moves its heartbeat on a timer while a case runs, so a stale heartbeat
        means no process owns the row. `interrupted`, not `failed`: the cases recorded so far are
        real, and the run can be resumed from them.
        """
        cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_seconds)).isoformat()
        stmt = (
            update(eval_runs)
            .where(eval_runs.c.status == "running", eval_runs.c.heartbeat_at < cutoff)
            .values(status="interrupted", finished_at=_now(), error=reason)
        )
        with self._engine.begin() as conn:
            return int(conn.execute(stmt).rowcount or 0)


def _row_to_eval_run(row: RowMapping) -> EvalRunRow:
    return EvalRunRow(
        id=row["id"],
        eval_set_id=row["eval_set_id"],
        target=row["target"],
        status=row["status"],
        source=row["source"],
        eval_set=json.loads(row["eval_set"]) if row["eval_set"] else {},
        report=json.loads(row["report"]) if row["report"] else {},
        diff=json.loads(row["diff"]) if row["diff"] else None,
        passed=int(row["passed"] or 0),
        total=int(row["total"] or 0),
        cost_usd=float(row["cost_usd"] or 0.0),
        error=row["error"],
        started_at=row["started_at"],
        heartbeat_at=row["heartbeat_at"],
        finished_at=row["finished_at"],
        stop_requested_at=row["stop_requested_at"],
    )
