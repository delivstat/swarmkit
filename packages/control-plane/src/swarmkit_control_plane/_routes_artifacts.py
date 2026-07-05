"""Artifact registry, governed deploy, and observability routes (docs 14-15)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request

from swarmkit_control_plane._aggregation import AggregationStore
from swarmkit_control_plane._artifacts import KINDS as ARTIFACT_KINDS
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._compat import incompatibility
from swarmkit_control_plane._deploy import DEPLOYABLE, DeployError
from swarmkit_control_plane._fntypes import (
    DeployFn,
)
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._schemas import (
    DeploymentRequest,
    DeployRequest,
    RegisterVersionRequest,
    ReportArtifactsRequest,
)


def _mount_deploy(
    app: FastAPI,
    registry: SqliteRegistry,
    artifacts: ArtifactStore,
    agg: AggregationStore,
    deploy: DeployFn,
) -> None:
    """Governed deploy of a published registry version onto an instance (doc 15 / doc 17 step 7).

    Operator-only (legislative; the version was already human-approved to publish). Mode A pushes to
    the instance's serve /api; Mode B enqueues a `deploy` command for the connector. Always audited.
    """

    @app.post("/instances/{instance_id}/deploy")
    async def deploy_artifact(
        instance_id: str, req: DeployRequest, request: Request
    ) -> dict[str, Any]:
        inst = registry.get(instance_id)
        if inst is None:
            raise HTTPException(404, "instance not found")
        if req.kind not in DEPLOYABLE:
            raise HTTPException(
                400, f"kind '{req.kind}' is not deployable — use {'/'.join(DEPLOYABLE)}"
            )
        ver = artifacts.get_version(req.kind, req.artifact_id, req.version)
        if ver is None:
            raise HTTPException(404, f"no such version {req.kind}/{req.artifact_id}@{req.version}")

        # Schema-compatibility gate: refuse deploying what the instance can't validate (doc 15).
        reason = incompatibility(str(ver.get("schema_version", "")), inst.schema_version)
        if reason is not None:
            raise HTTPException(409, f"schema-incompatible deploy: {reason}")

        content = ver["content"]

        if inst.connection == "direct":
            try:
                result = await deploy(
                    inst.endpoint, inst.token_ref, req.kind, req.artifact_id, content
                )
            except DeployError as exc:
                raise HTTPException(502, f"deploy failed: {exc}") from exc
            outcome: dict[str, Any] = {"mode": "direct", "result": result}
        else:
            cmd = registry.enqueue(
                instance_id, "deploy", {"kind": req.kind, "id": req.artifact_id, "body": content}
            )
            outcome = {"mode": "poll", "command_id": cmd.cmd_id}

        # Record the registry-intended version only AFTER the push/enqueue succeeds — a failed
        # Mode-A push must not leave a phantom "deployed vX" record that drift then reports.
        artifacts.set_deployment(instance_id, req.kind, req.artifact_id, req.version)

        principal = getattr(request.state, "principal", None)
        by = (getattr(principal, "subject", None) or "") if principal else ""
        agg.ingest(
            instance_id,
            "audit",
            [
                {
                    "id": uuid4().hex,
                    "ts": datetime.now(UTC).isoformat(),
                    "action": "artifact.deploy",
                    "kind": req.kind,
                    "artifact_id": req.artifact_id,
                    "version": req.version,
                    "by": by,
                }
            ],
        )
        return {"status": "ok", "version": req.version, **outcome}


def _mount_artifacts(app: FastAPI, store: ArtifactStore) -> None:
    """Artifact registry: versioned artifacts + provenance, deployments, drift (doc 15)."""

    def _check_kind(kind: str) -> None:
        if kind not in ARTIFACT_KINDS:
            raise HTTPException(404, f"unknown kind '{kind}' — use {'/'.join(ARTIFACT_KINDS)}")

    @app.post("/artifacts/{kind}/{artifact_id}/versions")
    async def register_version(
        kind: str, artifact_id: str, req: RegisterVersionRequest
    ) -> dict[str, Any]:
        _check_kind(kind)
        return store.register_version(
            kind,
            artifact_id,
            content=req.content,
            authored_by=req.authored_by,
            schema_version=req.schema_version,
            version=req.version,
        )

    @app.get("/artifacts")
    async def list_artifacts() -> list[dict[str, Any]]:
        return store.list_artifacts()

    @app.get("/artifacts/{kind}/{artifact_id}/versions")
    async def list_versions(kind: str, artifact_id: str) -> list[dict[str, Any]]:
        _check_kind(kind)
        return store.list_versions(kind, artifact_id)

    @app.get("/artifacts/{kind}/{artifact_id}/versions/{version}")
    async def get_version(kind: str, artifact_id: str, version: str) -> dict[str, Any]:
        _check_kind(kind)
        found = store.get_version(kind, artifact_id, version)
        if found is None:
            raise HTTPException(404, "version not found")
        return found

    @app.put("/instances/{instance_id}/deployments/{kind}/{artifact_id}")
    async def set_deployment(
        instance_id: str, kind: str, artifact_id: str, req: DeploymentRequest
    ) -> dict[str, str]:
        _check_kind(kind)
        try:
            store.set_deployment(instance_id, kind, artifact_id, req.version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"status": "ok"}

    @app.get("/instances/{instance_id}/deployments")
    async def list_deployments(instance_id: str) -> list[dict[str, Any]]:
        return store.list_deployments(instance_id)

    @app.post("/instances/{instance_id}/artifacts/report")
    async def report_artifacts(instance_id: str, req: ReportArtifactsRequest) -> dict[str, int]:
        return {"reported": store.report(instance_id, req.records)}

    @app.get("/instances/{instance_id}/drift")
    async def drift(instance_id: str) -> list[dict[str, Any]]:
        return store.drift(instance_id)


def _mount_observability(app: FastAPI, config: dict[str, str]) -> None:
    """Expose the configured collector + dashboard URLs for the fleet UI to link out (doc 14)."""

    @app.get("/observability")
    async def observability() -> dict[str, str]:
        # The collector endpoint instances send OTLP to; the Jaeger/Grafana URLs the UI deep-links.
        return {
            "collector_endpoint": config.get("collector_endpoint", ""),
            "jaeger_url": config.get("jaeger_url", ""),
            "grafana_url": config.get("grafana_url", ""),
        }
