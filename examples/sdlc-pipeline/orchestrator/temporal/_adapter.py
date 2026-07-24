"""The Temporal adapter — implements OrchestrationProvider over a Temporal client.

Owns the ``run_pipeline_stage`` activity (which calls the injected ``run_stage`` seam — the
StageRunner in tests/demo, a ``swarmkit serve`` client in production) and the start / signal /
gate / cancel / state operations as Temporal workflow calls. Not sandboxed — this is activity +
client code; only :mod:`._workflow` runs inside the deterministic workflow sandbox.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from .. import RunStage, SagaView
from ._workflow import PipelineWorkflow

_TASK_QUEUE = "sdlc-pipeline"


class TemporalOrchestrator:
    """Drive StageGraphs as durable Temporal workflows (one per correlation id)."""

    def __init__(
        self, client: Client, run_stage: RunStage, *, task_queue: str = _TASK_QUEUE
    ) -> None:
        self._client = client
        self._run_stage = run_stage
        self._task_queue = task_queue

    @activity.defn(name="run_pipeline_stage")
    async def run_stage_activity(self, params: dict[str, Any]) -> dict[str, Any]:
        outcome = await self._run_stage(str(params["correlation_id"]), dict(params["stage"]))
        return {"status": outcome.status, "artifact": outcome.artifact, "detail": outcome.detail}

    @property
    def activities(self) -> list[Any]:
        return [self.run_stage_activity]

    async def start(self, correlation_id: str, graph: dict[str, Any], initial_event: str) -> None:
        await self._client.start_workflow(
            PipelineWorkflow.run,
            {"graph": graph, "correlation_id": correlation_id, "initial_event": initial_event},
            id=correlation_id,
            task_queue=self._task_queue,
        )

    async def signal_event(self, correlation_id: str, event: str) -> None:
        handle = self._client.get_workflow_handle(correlation_id)
        await handle.signal(PipelineWorkflow.submit_event, event)

    async def resolve_gate(self, correlation_id: str, gate: str, *, approved: bool) -> None:
        handle = self._client.get_workflow_handle(correlation_id)
        await handle.signal(PipelineWorkflow.resolve_gate, args=[gate, approved])

    async def cancel(self, correlation_id: str) -> None:
        handle = self._client.get_workflow_handle(correlation_id)
        await handle.signal(PipelineWorkflow.cancel)

    async def state(self, correlation_id: str) -> SagaView:
        handle = self._client.get_workflow_handle(correlation_id)
        view: dict[str, Any] = await handle.query(PipelineWorkflow.view)
        return SagaView(
            correlation_id=str(view["correlation_id"]),
            status=str(view["status"]),
            current_stage=view["current_stage"],
            passed_stages=list(view["passed_stages"]),
            pending_gate=view["pending_gate"],
        )

    async def result(self, correlation_id: str) -> dict[str, Any]:
        """Await the pipeline's terminal saga view (test/demo convenience)."""
        handle = self._client.get_workflow_handle(correlation_id)
        return await handle.result()


def pipeline_worker(
    client: Client, orchestrator: TemporalOrchestrator, *, task_queue: str = _TASK_QUEUE
) -> Worker:
    """A Worker hosting the pipeline workflow + the orchestrator's stage activity."""
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[PipelineWorkflow],
        activities=orchestrator.activities,
    )


__all__ = ["TemporalOrchestrator", "pipeline_worker"]
