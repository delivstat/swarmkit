"""Artifact registry, governed deploy, and observability routes (docs 14-15).

The deploy route is thin: it reads the acting principal and delegates to :class:`DeployService`,
mapping its :class:`ServiceError` to a status code.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request

from swarmkit_control_plane._artifacts import KINDS as ARTIFACT_KINDS
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._schemas import (
    DeploymentRequest,
    DeployRequest,
    RegisterVersionRequest,
    ReportArtifactsRequest,
)
from swarmkit_control_plane._service import DeployService, ServiceError


def _mount_deploy(app: FastAPI, service: DeployService) -> None:
    """Governed deploy of a published registry version onto an instance (doc 15 / doc 17 step 7).

    Operator-only (legislative; the version was already human-approved to publish). Mode A pushes to
    the instance's serve /api; Mode B enqueues a `deploy` command for the connector. Always audited.
    """

    @app.post("/instances/{instance_id}/deploy")
    async def deploy_artifact(
        instance_id: str, req: DeployRequest, request: Request
    ) -> dict[str, Any]:
        principal = getattr(request.state, "principal", None)
        by = (getattr(principal, "subject", None) or "") if principal else ""
        try:
            return await service.deploy(
                instance_id=instance_id,
                kind=req.kind,
                artifact_id=req.artifact_id,
                version=req.version,
                by=by,
            )
        except ServiceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc


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
