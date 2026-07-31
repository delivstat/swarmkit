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

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from swarmkit_runtime.orchestration import RunStage, SagaStore, StageOutcome

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
    # Only upstream OUTPUTS thread downstream — not the per-stage `input` artifacts we also persist
    # for the inspector (which would otherwise feed a stage its own/earlier inputs).
    refs = [r for r in store.list(correlation_id) if r.endswith("/output")]
    parts = [c for ref in refs if (c := store.get(ref))]
    return "\n\n".join(parts)


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
    return _prior_input(artifact_store, correlation_id)


def build_pipeline_run_stage(
    runtime: WorkspaceRuntime,
    artifact_store: ArtifactStore,
    saga_store: SagaStore,
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
            stage_input = _stage_input(saga_store, artifact_store, correlation_id, stage)
            # Persist the resolved input alongside the output (name="input"), so the run inspector
            # can show exactly what this node received — recorded before the run so it survives a
            # failed stage too.
            artifact_store.put(correlation_id, sid, stage_input, name="input")
            result = await runtime.run(
                topology, stage_input, max_steps=max_steps, thread_id=correlation_id
            )
        except KeyError:
            return StageOutcome(status="failed", detail=f"unknown topology {topology!r}")
        except Exception as exc:  # surface any run error as a terminal outcome
            return StageOutcome(status="failed", detail=f"{type(exc).__name__}: {exc}")

        output = result.output or ""
        ref = artifact_store.put(correlation_id, sid, output)
        gated = bool(stage.get("gate") or stage.get("funnel"))
        return StageOutcome(
            status="parked" if gated else "completed",
            artifact=ref,
            artifact_bytes=len(output.encode()),
        )

    return run_stage


__all__ = ["build_pipeline_run_stage"]
