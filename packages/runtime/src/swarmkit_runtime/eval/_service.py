"""EvalService — the one way any front door runs an eval (design/details/eval-on-serve.md §2).

`swarmkit eval` and (from PR 3) serve's eval routes are thin over this. It owns the eval run's
durable row: created as `running` with a snapshot of the set, each case recorded the moment it is
scored, a heartbeat while a case runs, and a terminal status with the report and the diff against
the previous finished run. A process that dies mid-eval leaves a row the startup sweep marks
`interrupted`, and :meth:`EvalService.resume` continues it from the cases already recorded.

Per-case runs stay off ``JobService`` on purpose (the runner's ``# noqa: service-layer``): an eval
case is measurement, not work someone asked for. This service is the eval's own record instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from swarmkit_runtime.eval._compare import diff_report, latest_prior_report
from swarmkit_runtime.eval._errors import (
    EvalRunConflictError,
    EvalRunNotFoundError,
    EvalTargetNotFoundError,
)
from swarmkit_runtime.eval._loader import list_eval_sets, load_eval_set
from swarmkit_runtime.eval._models import EvalCaseResult, EvalReport, EvalSet
from swarmkit_runtime.eval._runner import _RuntimeLike, run_eval_set
from swarmkit_runtime.persistence import RESUMABLE_EVAL_STATUSES, EvalRunRow

#: How often a running eval moves its heartbeat while a case is in flight.
HEARTBEAT_INTERVAL_S = 30.0
#: A `running` row whose heartbeat is older than this has no live owner. Ten heartbeats: wide enough
#: that a busy process or a slow store write never gets a live eval swept, and independent of how
#: long one case takes — which is why the heartbeat ticks on a timer rather than per case.
STALE_AFTER_S = 300.0

OnCase = Callable[[EvalCaseResult], Awaitable[None] | None]
ShouldStop = Callable[[], bool | Awaitable[bool]]


@dataclass(frozen=True)
class EvalSetSummary:
    """One eval set, for a listing: what it is and how its last run went."""

    id: str
    name: str
    description: str
    target: str
    case_count: int
    last_run: EvalRunRow | None = None


class EvalService:
    """Run, resume, stop and read eval runs over one workspace's runtime and store."""

    def __init__(self, runtime: _RuntimeLike, store: Any, workspace_root: Path) -> None:
        self._runtime = runtime
        self._store = store
        self._root = workspace_root

    # ---- eval sets ------------------------------------------------------------

    def list_sets(self) -> list[EvalSetSummary]:
        """Every valid set under ``evals/``, each with its most recent run."""
        out: list[EvalSetSummary] = []
        for es in list_eval_sets(self._root):
            last = self._store.list_eval_runs(es.metadata.id, limit=1)
            out.append(
                EvalSetSummary(
                    id=es.metadata.id,
                    name=es.metadata.name or es.metadata.id,
                    description=es.metadata.description,
                    target=es.target,
                    case_count=len(es.cases),
                    last_run=last[0] if last else None,
                )
            )
        return out

    def get_set(self, ref: str) -> EvalSet:
        """A set by id, filename stem or path. Raises ``EvalSetNotFoundError`` or
        ``EvalSetInvalidError``."""
        return load_eval_set(self._root, ref)

    # ---- runs -----------------------------------------------------------------

    async def run(
        self,
        ref: str,
        *,
        source: str,
        on_case: OnCase | None = None,
        case_timeout_s: float | None = None,
        should_stop: ShouldStop | None = None,
        eval_run_id: str | None = None,
    ) -> EvalRunRow:
        """Run a set from the start and return its finished row.

        A set that does not load, or whose target is not a topology in the workspace, raises before
        any row is written — there is nothing to resume. Once the row exists it always ends in a
        terminal status, even when the eval itself fails.
        """
        row = await self.begin(ref, source=source, eval_run_id=eval_run_id)
        return await self.execute(
            row, on_case=on_case, case_timeout_s=case_timeout_s, should_stop=should_stop
        )

    async def begin(self, ref: str, *, source: str, eval_run_id: str | None = None) -> EvalRunRow:
        """Admit a run: load the set, check its target, write the `running` row. Nothing is scored.

        The first half of :meth:`run`, for a caller that answers before the cases run — serve's
        ``POST /evals/{id}/run`` returns 202 with this row, then hands it to :meth:`execute` in a
        task. Raises before any row exists when the set or its target is wrong.
        """
        eval_set = self.get_set(ref)
        self._check_target(eval_set)
        run_id = eval_run_id or str(uuid.uuid4())
        row: EvalRunRow = await asyncio.to_thread(
            self._store.create_eval_run,
            run_id,
            eval_set_id=eval_set.metadata.id,
            target=eval_set.target,
            source=source,
            eval_set=eval_set.model_dump(mode="json"),
            total=len(eval_set.cases),
        )
        return row

    async def resume(
        self,
        eval_run_id: str,
        *,
        on_case: OnCase | None = None,
        case_timeout_s: float | None = None,
        should_stop: ShouldStop | None = None,
    ) -> EvalRunRow:
        """Continue an `interrupted` or `stopped` run: only the cases without a result, on the same
        row, against the set *as it was run* — editing the file since changes nothing here.

        Refused for a run in any other state, and for one whose target topology is gone.
        """
        claimed = await self.begin_resume(eval_run_id)
        return await self.execute(
            claimed, on_case=on_case, case_timeout_s=case_timeout_s, should_stop=should_stop
        )

    async def begin_resume(self, eval_run_id: str) -> EvalRunRow:
        """Admit a resume: check the run can be resumed and claim it, back to `running`. The first
        half of :meth:`resume`, as :meth:`begin` is of :meth:`run`."""
        row = await asyncio.to_thread(self._store.get_eval_run, eval_run_id)
        if row is None:
            raise EvalRunNotFoundError(f"No eval run {eval_run_id!r}.")
        if row.status not in RESUMABLE_EVAL_STATUSES:
            raise EvalRunConflictError(
                f"Eval run {eval_run_id!r} is {row.status}; only an interrupted or stopped run "
                "can be resumed."
            )
        eval_set = EvalSet.model_validate(row.eval_set)
        self._check_target(eval_set)
        if not await asyncio.to_thread(self._store.claim_eval_resume, eval_run_id):
            raise EvalRunConflictError(
                f"Eval run {eval_run_id!r} was resumed or changed by another caller."
            )
        claimed: EvalRunRow | None = await asyncio.to_thread(self._store.get_eval_run, eval_run_id)
        assert claimed is not None
        return claimed

    def request_stop(self, eval_run_id: str) -> bool:
        """Ask a running eval to stop after its current case. False if it isn't running."""
        return bool(self._store.request_eval_stop(eval_run_id))

    def get_run(self, eval_run_id: str) -> EvalRunRow | None:
        row: EvalRunRow | None = self._store.get_eval_run(eval_run_id)
        return row

    def list_runs(self, eval_set_id: str | None = None, *, limit: int = 20) -> list[EvalRunRow]:
        rows: list[EvalRunRow] = self._store.list_eval_runs(eval_set_id, limit=limit)
        return rows

    def sweep_stale(self, reason: str, *, older_than_s: float = STALE_AFTER_S) -> int:
        """Mark runs no live process owns as `interrupted`. Returns how many."""
        return int(self._store.sweep_stale_eval_runs(older_than_s, reason))

    @staticmethod
    def report_of(row: EvalRunRow) -> EvalReport:
        """The run's report as a value object, cases in the order of the set as run."""
        order = {c.get("id"): i for i, c in enumerate(row.eval_set.get("cases", []))}
        cases = sorted(
            (EvalCaseResult.from_dict(c) for c in row.cases),
            key=lambda c: order.get(c.case_id, len(order)),
        )
        return EvalReport(
            eval_set_id=row.eval_set_id,
            target=row.target,
            cases=cases,
            started_at=row.started_at,
            finished_at=row.finished_at or "",
        )

    def _check_target(self, eval_set: EvalSet) -> None:
        workspace = getattr(self._runtime, "workspace", None)
        topologies = getattr(workspace, "topologies", None)
        if isinstance(topologies, Mapping) and eval_set.target not in topologies:
            available = ", ".join(sorted(topologies)) or "none"
            raise EvalTargetNotFoundError(
                f"Eval set {eval_set.metadata.id!r} targets topology {eval_set.target!r}, which "
                f"is not in this workspace. Available: {available}"
            )

    async def execute(
        self,
        row: EvalRunRow,
        *,
        on_case: OnCase | None = None,
        case_timeout_s: float | None = None,
        should_stop: ShouldStop | None = None,
    ) -> EvalRunRow:
        """Score an admitted row's remaining cases, against the set as snapshotted on the row, and
        finish it. The second half of :meth:`run` and :meth:`resume`; always ends the row in a
        terminal status, except on cancellation, which marks it `interrupted` and re-raises."""
        eval_set = EvalSet.model_validate(row.eval_set)
        run_id = row.id
        stop_seen = False

        async def _stop() -> bool:
            nonlocal stop_seen
            if should_stop is not None:
                asked = should_stop()
                if inspect.isawaitable(asked):
                    asked = await asked
                if asked:
                    stop_seen = True
                    return True
            if await asyncio.to_thread(self._store.eval_stop_requested, run_id):
                stop_seen = True
            return stop_seen

        async def _record(result: EvalCaseResult) -> None:
            await asyncio.to_thread(
                self._store.record_eval_case, run_id, result.to_dict(), result.cost_usd
            )
            if on_case is not None:
                pending = on_case(result)
                if inspect.isawaitable(pending):
                    await pending

        heartbeat = asyncio.create_task(self._heartbeat(run_id))
        try:
            await run_eval_set(
                self._runtime,
                eval_set,
                on_case=_record,
                case_timeout_s=case_timeout_s,
                should_stop=_stop,
                skip_case_ids=row.recorded_case_ids,
            )
        except asyncio.CancelledError:
            # The process is shutting down under us. Say so now rather than leave the row for the
            # sweep: the recorded cases stand, and the run can be resumed from them.
            await asyncio.shield(
                asyncio.to_thread(
                    self._store.finish_eval_run,
                    run_id,
                    "interrupted",
                    error="the eval was cancelled while it was running",
                )
            )
            raise
        except Exception as exc:
            await asyncio.to_thread(
                self._store.finish_eval_run, run_id, "error", error=str(exc)[:500]
            )
            return await self._reread(run_id)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        current = await self._reread(run_id)
        report = self.report_of(current)
        complete = len(report.cases) >= current.total
        if stop_seen and not complete:
            status = "stopped"
        elif report.failed:
            status = "failed"
        else:
            status = "succeeded"
        report_dict = {**report.to_dict(), "finished_at": datetime.now(UTC).isoformat()}
        diff = await asyncio.to_thread(self._diff, current, report) if complete else None
        await asyncio.to_thread(
            self._store.finish_eval_run, run_id, status, report=report_dict, diff=diff
        )
        return await self._reread(run_id)

    # ---- internals ------------------------------------------------------------

    def _diff(self, row: EvalRunRow, report: EvalReport) -> dict[str, Any] | None:
        """The diff against the newest run of the set that finished before this one started.

        A store with no prior run falls back to the newest legacy report file under
        ``.swarmkit/eval-results/``, so history from before eval runs were stored still counts.
        """
        prior = self._store.latest_finished_eval_run(
            row.eval_set_id, exclude_id=row.id, finished_before=row.started_at
        )
        prev: dict[str, object] | None = (
            prior.report if prior is not None else latest_prior_report(self._root, row.eval_set_id)
        )
        if prev is None:
            return None
        return {
            "prior_eval_run_id": prior.id if prior is not None else None,
            **dataclasses.asdict(diff_report(prev, report)),
        }

    async def _heartbeat(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._store.touch_eval_run, run_id)

    async def _reread(self, run_id: str) -> EvalRunRow:
        row: EvalRunRow | None = await asyncio.to_thread(self._store.get_eval_run, run_id)
        assert row is not None
        return row
