"""HTTP pipeline-run (saga) status endpoints — the read half of the pipeline surface
(design/details/bundled-pipeline-orchestrator.md §4).

Serve reads saga state from the shared store (it never imports the controller/engine) and lazy-reads
a node's artifact from the ArtifactStore on selection. The CLI (`swarmkit pipeline sagas`/`status`)
and the UI Runs view consume these. Read-only.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from swarmkit_runtime.artifacts import artifact_ref
from swarmkit_runtime.orchestration import SagaState


def _summary(s: SagaState) -> dict[str, Any]:
    return {
        "correlation_id": s.correlation_id,
        "graph": s.graph_id,
        "status": s.status,
        "current_stage": s.current_stage,
        "passed_stages": s.passed_stages,
        "pending_gate_stage": s.pending_gate_stage,
        "tag": s.tag,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def _detail(s: SagaState) -> dict[str, Any]:
    return {
        **_summary(s),
        "input": s.input,  # the pipeline payload that seeded the run (first stage's input)
        "artifacts": s.artifacts,  # stage -> reference (content fetched lazily per node)
        "attempts": s.attempts,
        "timeline": [
            {"seq": t.seq, "at": t.at, "stage": t.stage_id, "kind": t.kind, "detail": t.detail}
            for t in s.timeline
        ],
    }


def _register_saga_routes(app: FastAPI) -> None:
    def _store(request: Request) -> Any:
        store = getattr(request.app.state, "saga_store", None)
        if store is None:
            raise HTTPException(404, "This workspace has no pipeline orchestrator store")
        return store

    @app.get("/pipelines/sagas")
    def list_sagas(
        request: Request,
        status: str = "all",
        graph: str | None = None,
        q: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        sagas = _store(request).list(status=status, graph=graph, query=q, limit=limit)
        return {"sagas": [_summary(s) for s in sagas]}

    @app.get("/pipelines/sagas/{correlation_id}")
    def get_saga(request: Request, correlation_id: str) -> dict[str, Any]:
        saga = _store(request).get(correlation_id)
        if saga is None:
            raise HTTPException(404, f"No pipeline run {correlation_id!r}")
        return _detail(saga)

    @app.get("/pipelines/sagas/{correlation_id}/node/{stage}")
    def get_node_artifact(request: Request, correlation_id: str, stage: str) -> dict[str, Any]:
        saga = _store(request).get(correlation_id)
        if saga is None:
            raise HTTPException(404, f"No pipeline run {correlation_id!r}")
        ref = saga.artifacts.get(stage)
        artifacts = getattr(request.app.state, "artifact_store", None)
        content = artifacts.get(ref) if (ref and artifacts is not None) else None
        # The input this node received is persisted next to its output (name="input"); read it
        # lazily too so the inspector can show input -> artifact for the stage.
        input_ref = artifact_ref(correlation_id, stage, name="input")
        input_content = artifacts.get(input_ref) if artifacts is not None else None
        return {
            "stage": stage,
            "ref": ref,
            "content": content,
            "input_ref": input_ref if input_content is not None else None,
            "input": input_content,
        }
