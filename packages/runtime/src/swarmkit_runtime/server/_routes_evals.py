"""Eval sets and eval runs on serve (design/details/eval-on-serve.md §5).

Two groups, both thin:

- ``/api/evals`` — eval sets as an artifact kind. Reads go through :class:`EvalService` (which pairs
  each set with its last run); writes through :class:`ArtifactService`, which validates the file
  and restores the previous one on failure. Being under ``/api/*``, reads are the ``read`` tier and
  writes ``admin`` (``_helpers._required_action``) — no new scope.
- ``/evals/...`` — runs. ``POST /evals/{id}/run`` admits the run (unknown set 404, bad target 422,
  API tier 409, no free slot 429), answers 202 with the row, and scores the cases in a task that
  holds **one** slot of ``app.state.job_semaphore`` for the whole eval. Cases run one at a time
  inside it, each bounded by the serve's ``timeout_seconds``. The tasks are kept in a registry on
  ``app.state`` so shutdown can cancel them — which marks each row ``interrupted``, resumable.

Per-case runs stay off ``JobService`` on purpose (the runner's ``# noqa: service-layer``): an eval
is measurement, not work someone asked for, and :class:`EvalService` is its own durable record.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from swarmkit_runtime.eval import (
    STALE_AFTER_S,
    EvalRunConflictError,
    EvalRunNotFoundError,
    EvalService,
    EvalSetInvalidError,
    EvalSetNotFoundError,
    EvalSetSummary,
    EvalTargetNotFoundError,
)
from swarmkit_runtime.persistence import EvalRunRow

from ._helpers import _get_runtime
from ._services import ArtifactService, InvalidArtifactError, ServiceError

logger = logging.getLogger("swarmkit.server")

_SWEEP_REASON = "the server restarted while this eval was running"

#: A set id from a URL. `load_eval_set` also accepts a file path — right for the CLI, never for a
#: request — so an id that is not lowercase-kebab is refused before it reaches the loader.
_SET_ID = re.compile(r"^[a-z][a-z0-9-]*$")


def _check_set_id(set_id: str) -> None:
    if not _SET_ID.match(set_id):
        raise HTTPException(status_code=404, detail=f"No eval set {set_id!r}")


def _sweep_stale_eval_runs(store: Any) -> None:
    """Mark eval runs no live process owns as `interrupted` — the eval twin of `_sweep_stale_jobs`.

    Bounded by the heartbeat, not by "everything running": an instance sharing this store may be
    running one right now, and its heartbeat is fresh. Best-effort, like the job sweep.
    """
    if store is None:
        return
    try:
        swept = store.sweep_stale_eval_runs(STALE_AFTER_S, _SWEEP_REASON)
    except Exception:
        logger.warning("could not close eval runs left running by a previous process")
        return
    if swept:
        logger.info(
            "Marked %d eval run(s) interrupted: left running by a previous process. "
            "POST /evals/runs/{id}/resume continues one.",
            swept,
        )


async def cancel_eval_tasks(app: FastAPI) -> None:
    """Cancel every eval this process is running, at shutdown. Each marks its own row
    `interrupted` as it unwinds, so nothing is left for the next start's sweep."""
    tasks: dict[str, asyncio.Task[Any]] = getattr(app.state, "eval_tasks", {})
    for task in list(tasks.values()):
        task.cancel()
    for task in list(tasks.values()):
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def _set_json(s: EvalSetSummary) -> dict[str, Any]:
    last = s.last_run
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "target": s.target,
        "case_count": s.case_count,
        "last_run": None
        if last is None
        else {
            "id": last.id,
            "status": last.status,
            "passed": last.passed,
            "total": last.total,
            "pass_rate": round(last.passed / last.total, 4) if last.total else 0.0,
            "started_at": last.started_at,
            "finished_at": last.finished_at,
        },
    }


def _invalid(exc: InvalidArtifactError) -> JSONResponse:
    return JSONResponse(status_code=422, content=exc.body())


def _register_eval_routes(app: FastAPI, workspace_path: Path, artifacts: ArtifactService) -> None:  # noqa: PLR0915
    """Register `/api/evals` (sets) and `/evals/...` (runs)."""

    def _service(request: Request) -> EvalService:
        store = getattr(request.app.state, "store", None)
        if store is None:
            raise HTTPException(status_code=503, detail="No store: eval runs need one")
        return EvalService(_get_runtime(request), store, workspace_path)

    def _tasks(request: Request) -> dict[str, asyncio.Task[Any]]:
        tasks: dict[str, asyncio.Task[Any]] | None = getattr(request.app.state, "eval_tasks", None)
        if tasks is None:
            tasks = {}
            request.app.state.eval_tasks = tasks
        return tasks

    def _topologies(request: Request) -> Any:
        return _get_runtime(request).workspace.topologies

    async def _start(
        request: Request,
        admit: Any,  # (EvalService) -> Awaitable[EvalRunRow]
    ) -> JSONResponse:
        """Admit an eval and run it in the background under one capacity slot.

        The order matters. The API tier refuses first (it executes nothing). Then the slot is taken
        — checked and acquired with no await between, so two requests cannot both see it free —
        and only then is the row written, so a 429 never leaves a `running` row behind. A refusal
        after the slot is taken gives it back.
        """
        if getattr(request.app.state, "enqueue_only", False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This serve is the API tier (--role api) and executes no work. Run evals on an "
                    "all-in-one `swarmkit serve`, or with `swarmkit eval` on the CLI."
                ),
            )
        service = _service(request)
        semaphore: asyncio.Semaphore | None = getattr(request.app.state, "job_semaphore", None)
        if semaphore is not None:
            if semaphore.locked():
                raise HTTPException(
                    status_code=429, detail="Max concurrent jobs reached. Try again later."
                )
            await semaphore.acquire()  # free, so this returns without suspending
        try:
            row: EvalRunRow = await admit(service)
        except BaseException:
            if semaphore is not None:
                semaphore.release()
            raise

        cfg = getattr(request.app.state, "server_config", None)
        timeout = float(getattr(cfg, "timeout_seconds", 0) or 0) or None
        tasks = _tasks(request)

        async def _drive() -> None:
            try:
                await service.execute(row, case_timeout_s=timeout)
            except asyncio.CancelledError:
                raise
            except Exception:  # execute ends the row itself; this only keeps the task quiet
                logger.warning("eval run %s raised", row.id, exc_info=True)
            finally:
                tasks.pop(row.id, None)
                if semaphore is not None:
                    semaphore.release()

        tasks[row.id] = asyncio.create_task(_drive(), name=f"eval-run-{row.id}")
        return JSONResponse(
            status_code=202,
            content={"eval_run_id": row.id, "status": row.status, "eval_set_id": row.eval_set_id},
        )

    # ---- eval sets: /api/evals -------------------------------------------------

    @app.get("/api/evals")
    async def list_eval_sets(request: Request) -> list[dict[str, Any]]:
        """Every valid eval set, each with its last run's status and pass rate."""
        service = _service(request)
        sets = await asyncio.to_thread(service.list_sets)
        return [_set_json(s) for s in sets]

    @app.get("/api/evals/{set_id}")
    async def get_eval_set(set_id: str, request: Request) -> dict[str, Any]:
        """One set, parsed."""
        _check_set_id(set_id)
        try:
            es = _service(request).get_set(set_id)
        except EvalSetNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except EvalSetInvalidError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        out: dict[str, Any] = es.model_dump(mode="json", exclude_none=True)
        return out

    @app.get("/api/evals/{set_id}/yaml")
    async def get_eval_set_yaml(set_id: str) -> dict[str, str]:
        """The set's YAML, as written — found even when it no longer validates, so it can be
        fixed."""
        try:
            return {"yaml": artifacts.eval_set_yaml(set_id)}
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @app.post("/api/evals")
    async def create_eval_set(request: Request) -> Any:
        """Create a set from YAML; its file is `evals/<metadata.id>.yaml`."""
        body = await request.json()
        try:
            return artifacts.create_eval_set(body.get("yaml", ""), _topologies(request))
        except InvalidArtifactError as exc:
            return _invalid(exc)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @app.put("/api/evals/{set_id}")
    async def put_eval_set(set_id: str, request: Request) -> Any:
        """Replace (or create) a set. 422 — the previous file kept — when it does not validate."""
        body = await request.json()
        try:
            return artifacts.put_eval_set(set_id, body.get("yaml", ""), _topologies(request))
        except InvalidArtifactError as exc:
            return _invalid(exc)

    @app.delete("/api/evals/{set_id}")
    async def delete_eval_set(set_id: str) -> dict[str, Any]:
        """Remove a set's file. Its run history stays."""
        try:
            return artifacts.delete_eval_set(set_id)
        except ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    # ---- eval runs: /evals/... -------------------------------------------------
    # The literal `runs` routes are registered before `/evals/{set_id}/run`; the shapes differ
    # anyway (`/evals/runs/{id}` vs `/evals/{id}/run`), so no set id can shadow them.

    @app.get("/evals/runs")
    async def list_eval_runs(
        request: Request,
        set_id: str | None = Query(default=None, alias="set"),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Recent eval runs, newest first; `?set=` filters to one set."""
        rows = await asyncio.to_thread(
            _service(request).list_runs, set_id, limit=max(1, min(limit, 200))
        )
        return [r.summary() for r in rows]

    @app.get("/evals/runs/{run_id}")
    async def get_eval_run(run_id: str, request: Request) -> dict[str, Any]:
        """Status, the set as run, the cases so far, the report, the diff and the cost."""
        row = await asyncio.to_thread(_service(request).get_run, run_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No eval run {run_id!r}")
        return row.detail()

    @app.post("/evals/runs/{run_id}/stop")
    async def stop_eval_run(run_id: str, request: Request) -> dict[str, Any]:
        """Stop after the current case — in this process or any other sharing the store."""
        service = _service(request)
        if await asyncio.to_thread(service.request_stop, run_id):
            return {"eval_run_id": run_id, "stop_requested": True}
        row = await asyncio.to_thread(service.get_run, run_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No eval run {run_id!r}")
        raise HTTPException(
            status_code=409, detail=f"Eval run {run_id!r} is {row.status}, not running"
        )

    @app.post("/evals/runs/{run_id}/resume", status_code=202)
    async def resume_eval_run(run_id: str, request: Request) -> JSONResponse:
        """Continue an interrupted or stopped run from the cases it has not scored."""

        async def _admit(service: EvalService) -> EvalRunRow:
            try:
                return await service.begin_resume(run_id)
            except EvalRunNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (EvalRunConflictError, EvalTargetNotFoundError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        return await _start(request, _admit)

    @app.post("/evals/{set_id}/run", status_code=202)
    async def run_eval(set_id: str, request: Request) -> JSONResponse:
        """Start a run of a set: 202 with `eval_run_id`; poll `GET /evals/runs/{id}`."""

        async def _admit(service: EvalService) -> EvalRunRow:
            _check_set_id(set_id)
            try:
                return await service.begin(set_id, source="serve")
            except EvalSetNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (EvalSetInvalidError, EvalTargetNotFoundError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        return await _start(request, _admit)
