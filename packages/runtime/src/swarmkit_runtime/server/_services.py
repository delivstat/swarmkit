"""Runtime server service layer — ``JobService`` + ``ArtifactService``.

The business logic behind the run/webhook and artifact-CRUD routes, lifted out of the route
closures so it is unit-testable without HTTP — and so the run/webhook path (canary resolution,
capacity check, job creation + persistence + background start) lives in one place instead of being
duplicated across the two handlers.

A service owns no HTTP: it takes plain arguments (the route reads app-state — the live runtime,
canary router, store, semaphore — and passes them in) and raises :class:`ServiceError` subclasses
whose ``status`` the route maps to a code. ``ArtifactService`` mutation methods return the rebuilt
``WorkspaceRuntime`` (or ``None``) for the route to install on ``app.state`` — the one HTTP concern
that stays in the route.
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from swarmkit_runtime._input_schema import InputValidationError, check_entry_input
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.attachments import AttachmentError, resolve_all
from swarmkit_runtime.canary import CanaryRouter
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.persistence import Store
from swarmkit_runtime.resolver import resolve_workspace

from ._config import ServerCfg
from ._jobs import Job, JobStore, _start_job

logger = logging.getLogger("swarmkit.server")

_ARTIFACT_DIRS = {
    "topology": "topologies",
    "skill": "skills",
    "archetype": "archetypes",
    "funnel": "funnels",
    "stage-graph": "pipelines",
    "contract": "contracts",
    "role": "roles",
}


def _workload_class(topology: Any) -> str:
    """ "harness" if any agent in the topology's tree runs on a harness executor, else "model"
    (worker-fairness.md). Routes the job to the matching worker pool so long harness runs and short
    model runs do not share one FIFO. Defensive over stubs: an object without an executor is
    "model"."""

    def _walk(agent: Any) -> bool:
        if getattr(getattr(agent, "executor", None), "kind", "model") == "harness":
            return True
        return any(_walk(child) for child in getattr(agent, "children", ()) or ())

    root = getattr(topology, "root", None)
    return "harness" if root is not None and _walk(root) else "model"


def _label_priority(labels: dict[str, str] | None) -> int:
    """Queue priority from a ``priority`` label (higher first); 0 when absent or unparseable."""
    try:
        return int((labels or {}).get("priority", 0))
    except (TypeError, ValueError):
        return 0


class ServiceError(Exception):
    """Base for server service domain errors. ``status`` is the HTTP code the route maps it to."""

    status = 400


class NotFoundError(ServiceError):
    status = 404


class BusyError(ServiceError):
    status = 429


class InvalidRequestError(ServiceError):
    """The request was well-formed but its contents cannot be accepted.

    422 rather than 400 because the body parsed and validated as a shape — an attachment path that
    does not exist or escapes the workspace is a semantic refusal, and telling the two apart is the
    difference between "fix your JSON" and "fix your path".
    """

    status = 422


#: How stale the queue-depth reading behind queue-mode admission may be, in seconds.
QUEUE_DEPTH_MAX_AGE_S = 0.25


class _QueueDepthGauge:
    """The queued backlog depth that queue-mode admission checks, without a ``COUNT(*)`` per
    request (issue 1084).

    Re-read from the store at most every :data:`QUEUE_DEPTH_MAX_AGE_S`. Between reads it adds what
    this process did since: inserts that landed, and inserts still in flight. In-flight places are
    kept apart from the reading because a re-read cannot see an insert that has not committed —
    folding them into the reading is how a 1,000 bound admitted 1,001. Within one process the
    depth can only over-count (an insert that commits just before a re-read is briefly counted
    twice), so the bound holds.

    What it cannot see between reads is other API processes' admissions and workers' claims; the
    bound can overshoot by what other processes admit in one window. It protects against an
    unbounded backlog, not an exact one — a round trip per request was the price of exactness,
    and the 429 path paid it too.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._read = 0  # the store's count at the last read
        self._since = 0  # this process's inserts that landed after it
        self._in_flight = 0  # admitted, insert not finished
        self._read_at = float("-inf")

    def depth(self, store: Store) -> int:
        now = self._clock()
        if now - self._read_at >= QUEUE_DEPTH_MAX_AGE_S:
            self._read = store.count_jobs("queued")
            self._since = 0
            self._read_at = now
        return self._read + self._since + self._in_flight

    def reserve(self) -> None:
        self._in_flight += 1

    def landed(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)
        self._since += 1

    def release(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)


# One per store (one per database): JobService is built per call site, the backlog is per store.
_QUEUE_GAUGES: weakref.WeakKeyDictionary[Store, _QueueDepthGauge] = weakref.WeakKeyDictionary()


def _queue_gauge(store: Store) -> _QueueDepthGauge:
    gauge = _QUEUE_GAUGES.get(store)
    if gauge is None:
        gauge = _QUEUE_GAUGES[store] = _QueueDepthGauge()
    return gauge


def _log_if_unobserved(task: asyncio.Future[Any]) -> None:
    """A submit whose request was cancelled still finishes (see ``JobService.start``); if it then
    fails, nobody is awaiting it — log it rather than leave asyncio's "never retrieved" warning."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("a submit failed after its request was cancelled: %s", exc)


class JobService:
    """Start topology runs as background jobs — the shared logic behind /run and /hooks.

    The route reads the per-request app-state (live runtime, canary router, store, semaphore,
    server config) and passes it in; the service owns canary resolution, the capacity gate, and
    job creation + persistence + background start.
    """

    def __init__(self, job_store: JobStore) -> None:
        self._jobs = job_store

    def resolve_topology(
        self, rt: WorkspaceRuntime, canary: CanaryRouter | None, topology_name: str
    ) -> tuple[str, str | None]:
        """Resolve a request name to the topology to run + the canary version (if any).

        Raises :class:`NotFoundError` if neither the canary-resolved name nor the bare name exists.
        """
        resolved_name = topology_name
        selected_version: str | None = None
        # Authoring namespace ids (swarmkit:author:*) resolve inside WorkspaceRuntime.run,
        # not in the user's workspace. Canary doesn't apply to them — they're bundled
        # with the runtime, not operator-authored — so skip the lookup entirely. #1045.
        from swarmkit_runtime.authoring._resolver import is_authoring_id  # noqa: PLC0415

        if is_authoring_id(topology_name):
            return topology_name, None
        if canary and canary.has_route(topology_name):
            selected_version = canary.select(topology_name)
            resolved_name = f"{topology_name}@{selected_version}"
        if resolved_name not in rt.workspace.topologies:
            if topology_name not in rt.workspace.topologies:
                available = sorted(rt.workspace.topologies.keys())
                raise NotFoundError(f"Topology '{topology_name}' not found. Available: {available}")
            resolved_name = topology_name
            selected_version = None
        return resolved_name, selected_version

    async def start(
        self,
        *,
        rt: WorkspaceRuntime,
        canary: CanaryRouter | None,
        store: Store | None,
        cfg: ServerCfg,
        semaphore: Any,
        topology_name: str,
        user_input: str,
        max_steps: int,
        correlation_id: str | None = None,
        labels: dict[str, str] | None = None,
        parent_job_id: str | None = None,
        attachments: list[Any] | None = None,
        source: str = "serve",
        enqueue_only: bool = False,
        queue_max_depth: int = 0,
        budget_override: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> Job:
        """Resolve, gate on capacity, create + persist the job, and start it in the background.

        *source* is the front door the run came through — ``serve`` for ``POST /run``, ``a2a``
        for the A2A task API — and is what the portal's Source field and ``tasks/list`` read.

        *attachments* are the caller's declared files, resolved here so a bad one is a 422 on the
        request rather than a job that fails a moment later.

        *enqueue_only* is the API tier's mode (``serve --role api``, worker-execution.md): resolve
        and persist the job as ``queued`` for a worker to claim, then return without executing it.
        The resolve still happens here so a bad topology or attachment is a 4xx on submit, not a
        run that only fails once a worker picks it up. Requires a durable store (Postgres); a
        worker cannot claim a job that was never written down.

        *queue_max_depth* bounds the backlog in queue mode: when this many runs are already
        ``queued``, a further submit is refused with 429 rather than accepted into an unbounded
        backlog that no worker may reach for an unbounded time. 0 (the default) means unbounded —
        the ``serve --role api`` CLI sets a non-zero default so the production path is bounded.
        """
        resolved_name, selected_version = self.resolve_topology(rt, canary, topology_name)
        # input_schema check at SUBMIT (input-schema.md), so a malformed request is a 422 here and
        # never becomes a job — the background run validates again at the WorkspaceRuntime.run
        # choke point (which also emits the audit event), but a caller must not poll a failed job
        # to learn its request was the wrong shape. Cheap, structural, before attachments.
        resolved_topo = rt.workspace.topologies[resolved_name]
        input_schema = getattr(getattr(resolved_topo, "raw", None), "input_schema", None)
        if input_schema:
            try:
                check_entry_input(user_input, input_schema)
            except InputValidationError as exc:
                raise InvalidRequestError(str(exc)) from exc
        # Resolved HERE rather than inside the background run, so a path that does not exist or
        # escapes the workspace is a 4xx on this request. Left to the job, the caller would get a
        # job id and have to poll to discover the file was never readable — which is the shape of
        # error people stop checking for. `WorkspaceRuntime.run` passes resolved attachments
        # through untouched, so nothing is read twice.
        # Short-circuited when there is nothing to resolve, so the overwhelmingly common path never
        # reaches for a workspace root it has no use for.
        resolved_attachments: list[Any] = []
        if attachments:
            try:
                resolved_attachments = resolve_all(attachments, rt.workspace_root)
            except AttachmentError as exc:
                raise InvalidRequestError(str(exc)) from exc
        # Admission, per mode. All-in-one: reject-when-full on the in-process semaphore. Queue mode:
        # the semaphore does not apply (workers, not this loop, execute), but an unbounded queue
        # would accept work that may never begin — so bound backlog depth and 429 when it is full.
        gauge: _QueueDepthGauge | None = None
        if enqueue_only:
            if queue_max_depth > 0 and store is not None:
                gauge = _queue_gauge(store)
                if gauge.depth(store) >= queue_max_depth:
                    raise BusyError(
                        f"Queue is full ({queue_max_depth} runs waiting). Try again later."
                    )
                gauge.reserve()
        elif semaphore is not None and semaphore.locked():
            raise BusyError("Max concurrent jobs reached. Try again later.")
        # From admission on, the submit runs to completion even if this request is cancelled. The
        # insert runs in a worker thread, and a thread cannot be cancelled: the row lands whatever
        # happens to the request. Left cancellable, an all-in-one run would be written `pending`
        # and never started, and a queued one would give back a queue place its row still holds.
        # The on-loop insert this replaced had no cancellation point at all; this keeps that.

        async def _admitted() -> Job:
            job = await self._jobs.create(resolved_name, user_input, job_id=job_id)
            job.version = selected_version
            job.correlation_id = correlation_id
            job.source = source
            job.labels = dict(labels or {})
            if enqueue_only and store is None:
                err = ServiceError(
                    "queue mode requires a durable store; a worker cannot claim an unpersisted job"
                )
                err.status = 500
                raise err
            if store:
                await self._persist(
                    store,
                    job,
                    resolved_topo,
                    user_input,
                    labels=labels,
                    parent_job_id=parent_job_id,
                    enqueue_only=enqueue_only,
                    gauge=gauge,
                )
            if enqueue_only:
                # Claimable now; a worker (swarmkit worker) drains the queue. The in-memory Job
                # mirrors the durable status so the submit response reads "queued".
                job.status = "queued"
                # Drop the in-memory stub: the API tier never executes this job, so its live copy
                # would stay "queued" forever and shadow the durable row a worker keeps current (GET
                # merges live-over-row). The returned object still shapes the submit response.
                await self._jobs.remove(job.id)
                return job
            _start_job(
                self._jobs,
                job,
                rt,
                max_steps,
                timeout_seconds=cfg.timeout_seconds,
                semaphore=semaphore,
                canary_router=canary,
                store=store,
                labels=labels,
                attachments=resolved_attachments,
                budget_override=budget_override,
            )
            return job

        admitted = asyncio.ensure_future(_admitted())
        admitted.add_done_callback(_log_if_unobserved)
        return await asyncio.shield(admitted)

    async def _persist(
        self,
        store: Store,
        job: Job,
        topology: Any,
        user_input: str,
        *,
        labels: dict[str, str] | None,
        parent_job_id: str | None,
        enqueue_only: bool,
        gauge: _QueueDepthGauge | None,
    ) -> None:
        """Write the job row in one insert, off the loop.

        In queue mode the row is born claimable: ``queued``, with the workload class
        (worker-fairness.md) that routes it to a worker pool and the priority (a ``priority``
        label, higher first) that orders it. This used to be an insert plus up to two updates, all
        on the loop — three transactions every other request waited behind (issue 1084). The queue
        place reserved at admission is settled here: counted as landed, or given back on failure
        along with the in-memory stub.
        """
        try:
            await asyncio.to_thread(
                store.create_job,
                job.id,
                job.topology,
                user_input,
                job.correlation_id,
                job.source,
                labels=labels,
                parent_job_id=parent_job_id,
                status="queued" if enqueue_only else "pending",
                version=job.version,
                job_class=_workload_class(topology) if enqueue_only else None,
                priority=_label_priority(labels) if enqueue_only else None,
            )
        except BaseException:
            if gauge is not None:
                gauge.release()
            await self._jobs.remove(job.id)
            raise
        if gauge is not None:
            gauge.landed()

    async def resume(
        self,
        *,
        rt: Any,
        store: Any,
        cfg: Any,
        semaphore: Any,
        canary: Any,
        job_id: str,
        durable: Any,
        max_steps: int,
    ) -> Any:
        """Continue a run that parked on a human gate.

        Sibling to :meth:`start` so both go through one place: the semaphore slot, the timeout and
        the deferral branch are shared, and a resumed run can park again.

        The in-memory job is what `execute_job` mutates, so a run this process did not start — after
        a restart, or from another instance — is rehydrated from its durable row first.
        """
        job = await self._jobs.get(job_id) or await self._jobs.adopt(durable)
        job.status = "running"
        job.error = None
        _start_job(
            self._jobs,
            job,
            rt,
            max_steps,
            timeout_seconds=cfg.timeout_seconds,
            semaphore=semaphore,
            canary_router=canary,
            store=store,
            resume=True,
        )
        return job


class ArtifactService:
    """Topology/skill/archetype YAML editing — find/read/write/validate/reload + read projections.

    Mutating methods return ``(result, new_runtime)``: the JSON result the route returns, and the
    rebuilt ``WorkspaceRuntime`` (or ``None`` when no reload was warranted / it failed) for the
    route to install on ``app.state.runtime``.
    """

    def __init__(self, workspace_path: Path) -> None:
        self._ws = workspace_path

    def _artifact_dir(self, kind: str) -> Path:
        return self._ws / _ARTIFACT_DIRS[kind]

    def find_file(self, kind: str, artifact_id: str) -> Path | None:
        d = self._artifact_dir(kind)
        if not d.is_dir():
            return None
        for f in d.rglob("*.yaml"):
            try:
                raw = yaml.safe_load(f.read_text()) or {}
                meta = raw.get("metadata", {})
                if artifact_id in (meta.get("id", ""), meta.get("name", "")):
                    return f
            except Exception:
                continue
        return None

    def read_yaml(self, kind: str, artifact_id: str) -> str:
        f = self.find_file(kind, artifact_id)
        if f is None:
            raise NotFoundError(f"{kind.capitalize()} '{artifact_id}' not found")
        return f.read_text()

    def validate_workspace(self) -> dict[str, Any]:
        try:
            ws = resolve_workspace(self._ws)
            return {
                "valid": True,
                "topologies": sorted(ws.topologies.keys()),
                "skills": sorted(ws.skills.keys()),
                "archetypes": sorted(ws.archetypes.keys()),
            }
        except ResolutionErrors as exc:
            return {
                "valid": False,
                "errors": [{"code": e.code, "message": e.message} for e in exc.errors],
            }

    def reload(self) -> WorkspaceRuntime | None:
        """Rebuild the runtime from disk. Returns the new runtime, or None on failure (logged)."""
        try:
            return WorkspaceRuntime.from_workspace_path(self._ws)
        except Exception:
            logger.warning("Runtime reload failed after CRUD write", exc_info=True)
            return None

    def put_yaml(
        self, kind: str, artifact_id: str, yaml_content: str, *, dry_run: bool, parse_check: bool
    ) -> tuple[dict[str, Any], WorkspaceRuntime | None]:
        """Write, validate, keep it only if valid (restoring the previous file otherwise); reload
        on success. A dry run validates the workspace as it is.

        *parse_check* short-circuits a YAML parse error into a structured result before touching
        disk (the topology editor pre-flights this; skills/archetypes rely on workspace validation).
        """
        if parse_check:
            try:
                yaml.safe_load(yaml_content)
            except Exception as exc:
                return {
                    "valid": False,
                    "errors": [{"code": "yaml.parse", "message": str(exc)}],
                }, None
        if dry_run:
            return self.validate_workspace(), None
        f = self.find_file(kind, artifact_id) or (self._artifact_dir(kind) / f"{artifact_id}.yaml")
        f.parent.mkdir(parents=True, exist_ok=True)
        previous = f.read_text() if f.exists() else None
        f.write_text(yaml_content)
        result = self.validate_workspace()
        if not result["valid"]:
            # Validation is of the workspace with the file in place, so the file is written first —
            # and used to STAY written when it failed. The running runtime kept the old artifact
            # (no reload), the disk held the broken one, and the next `swarmkit serve` refused the
            # whole workspace. Put back what was there.
            if previous is None:
                f.unlink()
            else:
                f.write_text(previous)
            return result, None
        return result, self.reload()

    def create_from_yaml(
        self, kind: str, yaml_content: str
    ) -> tuple[dict[str, Any], WorkspaceRuntime | None]:
        """Create a new artifact file from its metadata.name; reject a name clash / missing name."""
        try:
            parsed = yaml.safe_load(yaml_content)
            name = parsed.get("metadata", {}).get("name", "")
        except Exception as exc:
            return {"valid": False, "errors": [{"code": "yaml.parse", "message": str(exc)}]}, None
        if not name:
            return {
                "valid": False,
                "errors": [{"code": "missing.name", "message": "metadata.name required"}],
            }, None
        f = self._artifact_dir(kind) / f"{name}.yaml"
        if f.exists():
            return {
                "valid": False,
                "errors": [{"code": "exists", "message": f"'{name}' already exists"}],
            }, None
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(yaml_content)
        result = self.validate_workspace()
        if not result["valid"]:
            f.unlink()  # same rule as put_yaml: an artifact that failed validation is not created
            return result, None
        return result, self.reload()

    def delete(self, kind: str, artifact_id: str) -> tuple[dict[str, Any], WorkspaceRuntime | None]:
        f = self.find_file(kind, artifact_id)
        if f is None:
            raise NotFoundError(f"{kind.capitalize()} '{artifact_id}' not found")
        f.unlink()
        return {"deleted": True, "id": artifact_id}, self.reload()

    # ---- read projections over the live runtime -----------------------------

    def topology_detail(self, rt: WorkspaceRuntime, topology_id: str) -> dict[str, Any]:
        topo = rt.workspace.topologies.get(topology_id)
        if topo is None:
            raise NotFoundError(f"Topology '{topology_id}' not found")

        def _agent_to_dict(agent: Any) -> dict[str, Any]:
            result: dict[str, Any] = {
                "id": agent.id,
                "role": agent.role,
                "source_archetype": agent.source_archetype,
                "model": dict(agent.model) if agent.model else None,
                "skills": [s.id for s in agent.skills],
                # Ordering rules over those skills (design/details/skill-prerequisites.md). A
                # user-authored field that only YAML could show would be an incomplete schema
                # change — and this one changes what the agent is ALLOWED to do, which is exactly
                # what a reader of the topology needs to see.
                "requires": {k: list(v) for k, v in dict(agent.requires).items()},
                # Funnel gate reference (by id) — lets the composer light the "gated" badge
                # on server-loaded topologies (design/details/gate-funnel.md).
                "funnel": agent.funnel.id if getattr(agent, "funnel", None) else None,
            }
            if agent.children:
                result["children"] = [_agent_to_dict(c) for c in agent.children]
            return result

        return {
            "id": topo.id,
            "version": topo.raw.metadata.version,
            "description": getattr(topo.raw.metadata, "description", None),
            "resolved": _agent_to_dict(topo.root),
        }

    def archetype_detail(self, rt: WorkspaceRuntime, archetype_id: str) -> dict[str, Any]:
        arch = rt.workspace.archetypes.get(archetype_id)
        if arch is None:
            raise NotFoundError(f"Archetype '{archetype_id}' not found")
        raw = arch.raw
        defaults = getattr(raw, "defaults", None)
        return {
            "id": archetype_id,
            "name": raw.metadata.name,
            "description": getattr(raw.metadata, "description", ""),
            "role": raw.role,
            "defaults": {
                "model": (
                    dict(defaults.model) if defaults and getattr(defaults, "model", None) else None
                ),
                "skills": [
                    s if isinstance(s, str) else getattr(s, "capability", str(s))
                    for s in (getattr(defaults, "skills", None) or [])
                ]
                if defaults
                else [],
            },
        }

    def skill_detail(self, rt: WorkspaceRuntime, skill_id: str) -> dict[str, Any]:
        skill = rt.workspace.skills.get(skill_id)
        if skill is None:
            raise NotFoundError(f"Skill '{skill_id}' not found")
        raw = skill.raw
        impl = getattr(raw, "implementation", None)
        return {
            "id": skill_id,
            "name": raw.metadata.name,
            "description": getattr(raw.metadata, "description", ""),
            "category": getattr(raw.category, "value", str(raw.category)),
            "implementation_type": getattr(impl, "type", None) if impl else None,
        }
