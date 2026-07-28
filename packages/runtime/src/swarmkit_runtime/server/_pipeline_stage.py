"""The bundled run-stage execution seam (design/details/bundled-pipeline-orchestrator.md, slice 5b).

``app.state.pipeline_run_stage`` is the :data:`RunStage` the reference orchestrator calls over
``POST /pipelines/run-stage``. This is its production implementation: resolve the stage's topology,
run it as one bounded, governed SwarmKit run (correlated by ``correlation_id``), write the output to
the workspace :class:`ArtifactStore`, and return a domain-neutral :class:`StageOutcome`.

Content stays a runtime concern: the orchestrator only ever sees the returned artifact **reference**
(``<correlation_id>/<stage>/output``). Inter-stage threading is store-mediated — a stage's input is
assembled from the correlation's prior artifacts, so the orchestrator threads references, not bytes.

A stage carrying a funnel gate (``gate`` / ``funnel`` on the spec) returns ``parked`` once its
artifact exists: the run pauses on its human gate, resolved out of band by an operator act
(``swarmkit pipeline advance`` / the run inspector) that emits the ``gate`` event the controller
resumes on. An ungated stage returns ``completed``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from swarmkit_runtime.orchestration import RunStage, StageOutcome

if TYPE_CHECKING:
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
    from swarmkit_runtime.artifacts import ArtifactStore


def _stage_topology(stage: dict[str, Any]) -> str | None:
    """The topology (or agent) id a stage runs. ``None`` if the stage names neither."""
    topology = stage.get("topology") or stage.get("agent")
    return str(topology) if topology else None


def _stage_id(stage: dict[str, Any]) -> str:
    return str(stage.get("id") or stage.get("topology") or stage.get("agent") or "stage")


def _prior_input(store: ArtifactStore, correlation_id: str) -> str:
    """Assemble a stage's input from the correlation's already-produced artifacts.

    Store-mediated inter-stage threading: the run-stage seam reads upstream content itself, so the
    orchestrator threads only references. Empty for the first stage of a run.
    """
    refs = store.list(correlation_id)
    parts = [c for ref in refs if (c := store.get(ref))]
    return "\n\n".join(parts)


def build_pipeline_run_stage(
    runtime: WorkspaceRuntime,
    artifact_store: ArtifactStore,
    *,
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
        try:
            prior = _prior_input(artifact_store, correlation_id)
            result = await runtime.run(
                topology, prior, max_steps=max_steps, thread_id=correlation_id
            )
        except KeyError:
            return StageOutcome(status="failed", detail=f"unknown topology {topology!r}")
        except Exception as exc:  # surface any run error as a terminal outcome
            return StageOutcome(status="failed", detail=f"{type(exc).__name__}: {exc}")

        ref = artifact_store.put(correlation_id, sid, result.output or "")
        gated = bool(stage.get("gate") or stage.get("funnel"))
        return StageOutcome(status="parked" if gated else "completed", artifact=ref)

    return run_stage


__all__ = ["build_pipeline_run_stage"]
