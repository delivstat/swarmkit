"""The bundled run-stage execution seam (design/details/bundled-pipeline-orchestrator.md, slice 5b).

``app.state.pipeline_run_stage`` is the :data:`RunStage` the reference orchestrator calls over
``POST /pipelines/run-stage``. This is its production implementation: resolve the stage's topology,
run it as one bounded, governed SwarmKit run (correlated by ``correlation_id``), write the output to
the workspace :class:`ArtifactStore`, and return a domain-neutral :class:`StageOutcome`.

Content stays a runtime concern: the orchestrator only ever sees the returned artifact **reference**
(``<correlation_id>/<stage>/output``). Input is store-mediated — the **first** stage is seeded with
the pipeline's input payload (persisted on the saga from the ``start`` event); **downstream** stages
thread the correlation's prior artifacts. Either way the orchestrator threads references, not bytes.

A stage carrying a funnel gate (``gate`` / ``funnel`` on the spec) returns ``parked`` once its
artifact exists: the run pauses on its human gate, resolved out of band by an operator act
(``swarmkit pipeline advance`` / the run inspector) that emits the ``gate`` event the controller
resumes on. An ungated stage returns ``completed``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from swarmkit_runtime._run_scope import reset_in_pipeline_stage, set_in_pipeline_stage
from swarmkit_runtime.orchestration import RunStage, SagaStore, StageOutcome
from swarmkit_runtime.persistence import usage_fields

logger = logging.getLogger("swarmkit.pipeline")

if TYPE_CHECKING:
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
    from swarmkit_runtime.artifacts import ArtifactStore


def _stage_topology(stage: dict[str, Any]) -> str | None:
    """The topology (or agent) id a stage runs. ``None`` if the stage names neither."""
    topology = stage.get("topology") or stage.get("agent")
    return str(topology) if topology else None


def _stage_id(stage: dict[str, Any]) -> str:
    return str(stage.get("id") or stage.get("topology") or stage.get("agent") or "stage")


def _prior_input(
    store: ArtifactStore, correlation_id: str, stage_id: str = "", round_: int = 0
) -> str:
    """Assemble a stage's input from the correlation's already-produced artifacts.

    Store-mediated inter-stage threading: the run-stage seam reads upstream content itself, so the
    orchestrator threads only references. Empty for the first stage of a run.

    A stage's OWN previous output is separated out and wrapped, not concatenated with the upstream
    ones. On a re-run — a gate-driven rework — this used to hand the agent its own earlier draft as
    an unmarked block indistinguishable from upstream context. A harness re-run is a fresh process
    with no memory of writing it, so that reads as injected content; one refused a revision on
    exactly those grounds. The agent should see its draft, and should be told whose it is.
    """
    from swarmkit_runtime.artifacts import artifact_ref  # noqa: PLC0415
    from swarmkit_runtime.review._prior_output import render_prior_output  # noqa: PLC0415

    # Only upstream OUTPUTS thread downstream — not the per-stage `input` artifacts we also persist
    # for the inspector (which would otherwise feed a stage its own/earlier inputs).
    own_ref = artifact_ref(correlation_id, stage_id) if stage_id else ""
    refs = [r for r in store.list(correlation_id) if r.endswith("/output") and r != own_ref]
    parts = [c for ref in refs if (c := store.get(ref))]
    upstream = "\n\n".join(parts)

    own = store.get(own_ref) if own_ref else ""
    if not own:
        return upstream
    draft = render_prior_output(own, agent_id=stage_id, round_=round_, artifact_ref=own_ref)
    return f"{upstream}\n\n{draft}" if upstream else draft


def _stage_input(
    saga_store: SagaStore,
    artifact_store: ArtifactStore,
    correlation_id: str,
    stage: Mapping[str, Any] | None = None,
) -> str:
    """What one stage's topology run receives as its input, in precedence order.

    1. ``stage["input"]`` when the caller supplied one. ``RunStageRequest.stage`` is a free-form
       object, so a caller naturally passes ``input``; it used to be accepted and silently dropped.
    2. The saga's ``input`` — the payload carried on the `start` event — for the **first** stage,
       so a pipeline can be driven by an incoming payload and not just an opaque correlation id.
    3. The upstream artifacts, for every **downstream** stage.

    The correlation id is always available too (it is the run's `thread_id`).
    """
    supplied = str((stage or {}).get("input") or "")
    if supplied:
        return supplied
    saga = saga_store.get(correlation_id)
    if saga is not None and not saga.passed_stages:
        return saga.input
    stage_id = _stage_id(dict(stage)) if stage else ""
    attempts = int((getattr(saga, "attempts", None) or {}).get(stage_id, 0)) if saga else 0
    return _prior_input(artifact_store, correlation_id, stage_id, attempts)


def _saga_decisions(saga: Any, stage_id: str) -> list[Any]:
    """Decisions recorded on the saga timeline rather than the review store.

    Two routes carry a reviewer's comment. The review store is the good one:
    `/review/{item}/resolve` mints a resolved item and `decisions_for_gate` reads it. But
    `approvals:resolve` is reserved for
    human identity, so under `auth: none` that endpoint 403s and callers fall back to enqueuing a
    `rework` controller event — where the comment rides in the event payload and lands on the
    timeline.

    Both routes converge here, on one rendering mechanism, so comment delivery no longer depends on
    which auth provider serve happens to be running. The identity is labelled
    ``operator-override`` because that is what it is: a decision recorded without an authenticated
    reviewer behind it, and the agent should be able to weigh it accordingly.
    """
    from swarmkit_runtime.review._decisions import HumanDecision  # noqa: PLC0415

    out: list[Any] = []
    for entry in getattr(saga, "timeline", None) or []:
        meta = getattr(entry, "meta", None) or {}
        comment = str(meta.get("comment", "") or "")
        if not comment or entry.stage_id != stage_id:
            continue
        out.append(
            HumanDecision(
                outcome="changes-requested",
                identity=str(meta.get("identity", "") or "operator-override"),
                comment=comment,
                artifact_ref=str(meta.get("artifact_ref", "") or ""),
                round=int(meta.get("round", 0) or 0),
            )
        )
    return out


def _decisions_block(
    workspace_root: Path | None, correlation_id: str, stage_id: str, saga: Any = None
) -> str:
    """What humans decided about this stage so far, rendered for the agent.

    Empty until someone has decided something. On a rework loop this is how the agent learns WHY it
    is running again — without it, a re-run is indistinguishable from the first attempt and the
    agent would produce the same artifact.
    """
    from swarmkit_runtime.review import FileReviewQueue  # noqa: PLC0415
    from swarmkit_runtime.review._decisions import (  # noqa: PLC0415
        decisions_for_gate,
        render_decisions,
    )

    if workspace_root is None:
        return ""
    gate_id = f"{correlation_id}:{stage_id}"
    items = [
        i for i in FileReviewQueue(workspace_root).list_all() if i.output.get("gate_id") == gate_id
    ]
    decisions = decisions_for_gate(items) + _saga_decisions(saga, stage_id)
    if not decisions:
        return ""
    # The newest round's artifact is "current": anything from an earlier round is stale, which is
    # precisely the rework case this block exists to explain.
    current = max((i.artifact_ref for i in items if i.artifact_ref), default="", key=lambda r: r)
    newest_round = max((d.round for d in decisions), default=0)
    current = next(
        (i.artifact_ref for i in items if i.round == newest_round and i.artifact_ref), current
    )
    current = next(
        (d.artifact_ref for d in decisions if d.round == newest_round and d.artifact_ref), current
    )
    return render_decisions(
        decisions,
        gate_id=gate_id,
        current_artifact=current,
        current_round=newest_round,
    )


def stage_run_id(correlation_id: str, stage_id: str, attempt: int = 1) -> str:
    """The run id for one ATTEMPT at one stage of a pipeline run.

    Distinct per stage so traces, checkpoints and audit rows do not collide, prefixed with the
    correlation id so everything a run produced is still findable together.

    Distinct per ATTEMPT for the same reason it is distinct per stage. A rework — a human requests
    changes and the stage runs again — used to reuse the id, and everything keyed by it collided:

    * the job row's INSERT failed on the primary key, was swallowed as best-effort, and the second
      run left no record, so a rework never appeared in `/jobs`;
    * the closing UPDATE then succeeded against the FIRST row, leaving one chimera — round 1's
      input and start time beside round 2's output and cost, with round 1's spend simply gone;
    * the trace saves to ``{run_id}.json``, so the rework destroyed the trace of the draft the
      reviewer had actually objected to — the one a reader most wants when asking why.

    Attempt 1 is unsuffixed, so existing rows, traces and links keep resolving; ``@2`` onward marks
    a rework. ``@`` because the codebase already reads it as "this version of" (``hello@v2``), and
    it is safe in both a filename and a URL path segment.
    """
    return (
        f"{correlation_id}:{stage_id}" if attempt <= 1 else f"{correlation_id}:{stage_id}@{attempt}"
    )


def _revision_ref(ref: str, content: str) -> str:
    """A ref that identifies this REVISION of the artifact, not just its slot.

    The artifact store overwrites in place — `run-42/design/output` is the same string for v1 and
    v3 — so it cannot distinguish revisions, and gate rounds key on exactly that. Appending a
    content digest gives the property rounds need: it changes when the work changes, and a re-run
    that produces identical output keeps the same ref, so the roles are NOT re-asked to approve
    something they already approved.
    """
    digest = hashlib.sha256(content.encode()).hexdigest()[:12]
    return f"{ref}#{digest}"


async def _open_stage_gate(
    runtime: WorkspaceRuntime,
    correlation_id: str,
    stage_id: str,
    funnel_id: str,
    *,
    artifact_ref: str = "",
) -> None:
    """Fan the stage's funnel ``approve`` policy into role-tasks on the review queue.

    No-ops when the funnel declares no ``approve`` layer — a gate can legitimately be a checkpoint
    rather than a vote, and those keep releasing through ``swarmkit pipeline advance``.

    ``open_gate`` is idempotent, so a retried stage re-opens onto its existing items rather than
    clobbering approvals already cast. See the note's open question: those approvals were cast
    against the PREVIOUS artifact, which is arguably wrong — but silently discarding a human
    decision is worse, so today's behaviour is kept and documented.
    """
    from swarmkit_runtime.governance._approval import ApprovalPolicy  # noqa: PLC0415
    from swarmkit_runtime.review import FileReviewQueue  # noqa: PLC0415
    from swarmkit_runtime.review._multiparty import open_gate  # noqa: PLC0415

    # The run-stage seam is injectable (tests and demos supply a scripted runtime), so a resolved
    # workspace is not guaranteed. Without one there is no funnel to read: park as before rather
    # than failing a stage that would otherwise have succeeded.
    workspace = getattr(runtime, "workspace", None)
    if workspace is None:
        return
    funnel = workspace.funnels.get(funnel_id)
    approve = (funnel.spec.get("approve") if funnel is not None else None) or None
    if approve is None:
        logger.info(
            "stage %r parks on funnel %r, which declares no approve layer: no role-tasks opened "
            "(release with `swarmkit pipeline advance`)",
            stage_id,
            funnel_id,
        )
        return

    try:
        policy = ApprovalPolicy.from_dict(approve)
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("funnel %r has an unusable approve policy: %s", funnel_id, exc)
        return

    open_gate(
        FileReviewQueue(getattr(runtime, "workspace_root", Path("."))),
        gate_id=f"{correlation_id}:{stage_id}",
        topology_id=correlation_id,
        agent_id=stage_id,
        policy=policy,
        funnel_id=funnel_id,
        # Stamps every role-task with the artifact it is about, so a rework loop opens a new round
        # and decisions about an earlier draft stop counting toward quorum.
        artifact_ref=artifact_ref,
    )


def _record_stage_job(
    store: Any, run_id: str, correlation_id: str, topology: str, stage_input: str
) -> None:
    """Open a job row for a pipeline stage. Best-effort: losing the record never costs the run."""
    if store is None:
        return
    try:
        store.create_job(run_id, topology, stage_input, correlation_id, "pipeline")
    # A stage must run whether or not it can be recorded.
    except Exception:
        logger.warning("stage %s will not appear in jobs: could not create its row", run_id)


def _finish_stage_job(
    store: Any,
    run_id: str,
    status: str,
    *,
    output: str = "",
    error: str = "",
    usage: Any = None,
    diffs: dict[str, str] | None = None,
) -> None:
    """Close a stage's job row on every exit path, so none is left sitting at `running`."""
    if store is None:
        return
    fields: dict[str, Any] = {"status": status, "completed_at": datetime.now(tz=UTC).isoformat()}
    if diffs is not None:
        fields["diffs"] = diffs
    if output:
        fields["output"] = output
    if error:
        fields["error"] = error
    # Both usage sinks, through the one recorder — see persistence/_usage_recording.py.
    fields.update(usage_fields(usage, run_id, store))
    try:
        store.update_job(run_id, **fields)
    # Same one-directional rule on the way out.
    except Exception:
        logger.warning("could not record the outcome of stage %s", run_id)


def build_pipeline_run_stage(
    runtime: WorkspaceRuntime,
    artifact_store: ArtifactStore,
    saga_store: SagaStore,
    *,
    job_store: Any = None,
    max_steps: int = 50,
) -> RunStage:
    """A :data:`RunStage` that executes a stage's topology and persists its artifact.

    Injected as ``app.state.pipeline_run_stage``; the bundled ``swarmkit orchestrator`` drives it
    over ``POST /pipelines/run-stage``. Domain-neutral: it maps a generic stage spec to a bounded
    topology run and returns a reference-only outcome.
    """

    async def run_stage(correlation_id: str, stage: dict[str, Any]) -> StageOutcome:
        topology = _stage_topology(stage)
        if topology is None:
            return StageOutcome(status="failed", detail="stage names no topology/agent to run")

        sid = _stage_id(stage)
        # The controller increments `attempts[sid]` and SAVES before calling us, so this is the
        # attempt now starting — 1 on the first run, 2 on the first rework.
        saga = saga_store.get(correlation_id)
        attempt = int(getattr(saga, "attempts", {}).get(sid, 1) or 1)
        run_id = stage_run_id(correlation_id, sid, attempt)
        try:
            stage_input = _stage_input(saga_store, artifact_store, correlation_id, stage)
            # Human decisions about THIS stage (a rework loop) and about the upstream one (an
            # approval with conditions) both belong in what the agent reads.
            decisions = _decisions_block(
                getattr(runtime, "workspace_root", None),
                correlation_id,
                sid,
                saga,
            )
            if decisions:
                stage_input = f"{stage_input}\n\n{decisions}" if stage_input else decisions
            # Persist the resolved input alongside the output (name="input"), so the run inspector
            # can show exactly what this node received — recorded before the run so it survives a
            # failed stage too.
            artifact_store.put(correlation_id, sid, stage_input, name="input")
            # PER-STAGE thread, not the bare correlation id. The thread id becomes both the
            # LangGraph checkpoint thread AND the trace's run_id, and a trace saves to
            # `{run_id}.json` — so every stage of a run was overwriting the previous stage's trace,
            # leaving one file (the last stage's) and destroying per-stage cost and tool history as
            # the run progressed. Sharing a checkpoint thread was wrong independently: stages run
            # DIFFERENT topologies, so stage N inherited graph state from a different graph.
            # The prefix keeps every stage correlated to its run.
            # Record the stage as a JOB. A pipeline's actual topology executions used to leave no
            # job row — the only writers were serve's JobService and (from 1.150.0) the CLI — so
            # `/jobs` showed nothing for a pipeline while `/runs` showed only saga state. The work
            # itself, its output, its cost, were findable from neither.
            #
            # The row is keyed by the stage's run id (`<correlation>:<stage>`), which is also the
            # LangGraph thread and the trace's run_id, and carries `correlation_id` so one run's
            # stages can be selected directly rather than by parsing ids.
            _record_stage_job(job_store, run_id, correlation_id, topology, stage_input)
            # Marked as a STAGE for the duration of the run: this path opens the funnel's gate
            # itself below and parks the saga, so the in-node approve layer must stay advisory.
            # Without this the run would defer, never complete, and never reach the code that
            # opens the gate — a gated stage would simply stop.
            _stage_token = set_in_pipeline_stage(True)
            try:
                result = await runtime.run(
                    topology,
                    stage_input,
                    max_steps=max_steps,
                    thread_id=run_id,
                )
            finally:
                reset_in_pipeline_stage(_stage_token)
        except KeyError:
            _finish_stage_job(job_store, run_id, "failed", error=f"unknown topology {topology!r}")
            return StageOutcome(status="failed", detail=f"unknown topology {topology!r}")
        except Exception as exc:  # surface any run error as a terminal outcome
            _finish_stage_job(job_store, run_id, "failed", error=f"{type(exc).__name__}: {exc}")
            return StageOutcome(status="failed", detail=f"{type(exc).__name__}: {exc}")

        output = result.output or ""
        ref = artifact_store.put(correlation_id, sid, output)

        # A node that failed WITHOUT raising (a harness run that dies is a normal terminal event,
        # not an exception) used to reach here as an ordinary result. Its error string became the
        # stage's output artifact and then the NEXT stage's input: a downstream agent was
        # prompted with `[harness:<kind>] failure: no result event` and replied, reasonably,
        # "I'm ready to help — what would you like to work on?" The gate parked on THAT and asked a
        # human to approve work never attempted, while the saga read `parked` throughout.
        #
        # Three things went wrong at once: the pipeline advanced past a failure, the original error
        # was destroyed when the downstream output replaced it, and an agent was billed for a run
        # that could not succeed. So: stop here, keep the error as the failure reason rather than
        # as somebody's prompt, and do not open a gate on it.
        # Read defensively: `runtime.run` is a seam, and a caller may hand back any result-shaped
        # object. An older or third-party result simply has no failures to report — it must not
        # crash here, and it must not be treated as failed either.
        node_errors = getattr(result, "node_errors", None) or {}
        if node_errors:
            _finish_stage_job(
                job_store,
                run_id,
                "failed",
                error="; ".join(f"{node}: {why}" for node, why in sorted(node_errors.items())),
                usage=getattr(result, "usage", None),
                # Recorded on the FAILING path too: a harness that edited files and then failed
                # still produced work, and the worktree is gone either way.
                diffs=getattr(result, "diffs", None),
            )
            return StageOutcome(
                status="failed",
                artifact=ref,
                detail="; ".join(f"{node}: {why}" for node, why in sorted(node_errors.items())),
                artifact_bytes=len(output.encode()),
            )

        _finish_stage_job(
            job_store,
            run_id,
            "completed",
            output=output,
            usage=getattr(result, "usage", None),
            diffs=getattr(result, "diffs", None),
        )

        funnel_id = str(stage.get("gate") or stage.get("funnel") or "")
        if not funnel_id:
            return StageOutcome(
                status="completed", artifact=ref, artifact_bytes=len(output.encode())
            )

        # A gated stage OPENS its approval policy before parking, so the gate a human resolves is
        # the gate the run is actually waiting on (design/details/pipeline-gate-convergence.md).
        # Previously this parked with no policy evaluated, no role-tasks and no quorum — the run
        # could only be released by `swarmkit pipeline advance`, a single reserved-scope operator
        # act with none of the multi-party guarantees the funnel declares.
        await _open_stage_gate(
            runtime, correlation_id, sid, funnel_id, artifact_ref=_revision_ref(ref, output)
        )
        return StageOutcome(status="parked", artifact=ref, artifact_bytes=len(output.encode()))

    return run_stage


__all__ = ["build_pipeline_run_stage"]
