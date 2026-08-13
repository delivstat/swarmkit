"""A pipeline orchestrator that lives outside SwarmKit.

`design/details/extracting-the-pipeline.md`. SwarmKit runs a swarm over an input and returns a
governed, approved artifact; deciding what runs next belongs to the application. This is what that
looks like — a sequencer over the public HTTP API, with **no `swarmkit_runtime` import anywhere**.

The whole integration is the loop in :func:`run_stage`: start, poll, wait for a human, resume. A
Temporal workflow expresses it with activities and a signal; a cron job expresses it with `curl` and
`sleep`. That is the argument for the boundary — the sequencing is ordinary, and only the run and
the approval are not.

It is deliberately small. If demonstrating "orchestrate SwarmKit yourself" needed two thousand
lines, the boundary would be in the wrong place.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


class StageFailed(RuntimeError):
    """A stage did not produce an artifact — the application decides whether to retry."""


class StageRejected(RuntimeError):
    """A human rejected the stage's artifact at its gate."""


class Http(Protocol):
    """The three verbs this needs. A Protocol so a caller can supply httpx, requests, or a fake."""

    def get(self, path: str, **params: Any) -> Any: ...
    def post(self, path: str, json: Mapping[str, Any] | None = None) -> Any: ...


@dataclass(frozen=True)
class Stage:
    """One unit of work: a topology, and whether its output feeds the next one."""

    id: str
    topology: str
    #: stage ids whose artifacts are threaded into this stage's input
    after: tuple[str, ...] = ()


@dataclass
class Run:
    """What the orchestrator knows about one correlated sequence. Its own state, in its own store —
    a file, a table, a workflow's history. SwarmKit holds none of it."""

    correlation_id: str
    artifacts: dict[str, str] = field(default_factory=dict)
    jobs: dict[str, str] = field(default_factory=dict)


#: How often to ask. A gate waits on a person, so seconds are the wrong unit for impatience.
POLL_SECONDS = 2.0


def run_stage(
    http: Http,
    run: Run,
    stage: Stage,
    *,
    build_input: Callable[[Run, Stage], str],
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = POLL_SECONDS,
) -> str:
    """Run one stage to completion, waiting on a human if its funnel parks. Returns the artifact.

    The state machine, in full::

        POST /run/{topology}            -> job_id
        poll GET /jobs/{job_id}
            running    -> keep polling
            completed  -> take the artifact
            deferred   -> an approval is pending
            failed     -> the application's retry policy
        on deferred:
            find the gate  (GET /review, filtered by this run)
            poll GET /gates/{gate_id}
                approved  -> POST /jobs/{job_id}/resume, keep polling
                rejected  -> the stage fails
    """
    body = {"input": build_input(run, stage), "correlation_id": run.correlation_id}
    job_id = http.post(f"/run/{stage.topology}", json=body)["job_id"]
    run.jobs[stage.id] = job_id

    while True:
        job = http.get(f"/jobs/{job_id}")
        status = job["status"]

        if status == "completed":
            return _artifact(http, run, job_id, job)
        if status == "failed":
            raise StageFailed(f"{stage.id}: {job.get('error') or 'no reason recorded'}")
        if status == "deferred":
            _await_gate(http, job_id, sleep=sleep, poll_seconds=poll_seconds)
            continue
        sleep(poll_seconds)


def _await_gate(
    http: Http, job_id: str, *, sleep: Callable[[float], None], poll_seconds: float
) -> None:
    """Block until this run's gate resolves, then release the run.

    The gate id is not guessed: `GET /review` carries `run_id` on every role-task (1.184.0), so the
    gate belonging to THIS run is found rather than constructed from a naming convention that has
    two shapes.

    `GET /gates/{id}` applies quorum, distinct-approver counts and exclude_author. Counting approved
    role-tasks here instead would be reimplementing an approval policy this app cannot see — the
    policy lives in a funnel, which is SwarmKit's business.
    """
    gate_id = _gate_for(http, job_id)
    if gate_id is None:  # parked on something that is not a multi-party gate
        raise StageFailed(f"job {job_id} deferred with no gate to resolve")

    while True:
        state = http.get(f"/gates/{gate_id}")
        if state["status"] == "approved":
            http.post(f"/jobs/{job_id}/resume")
            return
        if state["status"] in {"rejected", "changes-requested"}:
            raise StageRejected(f"gate {gate_id} was {state['status']}")
        sleep(poll_seconds)


def _gate_for(http: Http, job_id: str) -> str | None:
    for item in http.get("/review"):
        if item.get("run_id") == job_id and item.get("gate_id"):
            return str(item["gate_id"])
    return None


def _artifact(http: Http, run: Run, job_id: str, job: Mapping[str, Any]) -> str:
    """This stage's output: its diff when a harness produced one, else the run's text output."""
    if job.get("diff_length"):
        return str(http.get(f"/jobs/{job_id}/diff")["diffs"])
    return str(job.get("output") or "")


def thread_upstream(run: Run, stage: Stage) -> str:
    """Default input builder: the artifacts of the stages this one comes after.

    Threading is the application's job. SwarmKit records artifacts under the correlation id and
    serves them; which of them a stage should see is a sequencing decision, and sequencing is here.
    """
    parts = [run.artifacts[dep] for dep in stage.after if dep in run.artifacts]
    return "\n\n".join(parts) if parts else run.correlation_id


def run_pipeline(
    http: Http,
    correlation_id: str,
    stages: Iterable[Stage],
    *,
    build_input: Callable[[Run, Stage], str] = thread_upstream,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = POLL_SECONDS,
) -> Run:
    """Drive a sequence of stages under one correlation id."""
    run = Run(correlation_id=correlation_id)
    for stage in stages:
        run.artifacts[stage.id] = run_stage(
            http,
            run,
            stage,
            build_input=build_input,
            sleep=sleep,
            poll_seconds=poll_seconds,
        )
    return run
