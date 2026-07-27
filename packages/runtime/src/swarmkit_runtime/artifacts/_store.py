"""ArtifactStore — where pipeline stage outputs + inter-stage payloads live
(design/details/bundled-pipeline-orchestrator.md §6).

Content is a **runtime** concern, not the orchestrator's: a stage's output is written here and
addressed by a reference derived from ``(correlation_id, stage)``; the orchestrator threads only the
reference. A workspace-configured, pluggable backend — ``database`` (default, zero-config on the
persistence engine), ``filesystem``, or ``s3`` (optional dep) — parallel to ``ModelProvider`` /
``GovernanceProvider``. Read lazily (the run inspector fetches a node's artifact only on selection).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


def artifact_ref(correlation_id: str, stage: str, name: str = "output") -> str:
    """The deterministic reference for a stage's artifact: ``<correlation_id>/<stage>/<name>``."""
    return f"{correlation_id}/{stage}/{name}"


class ArtifactStore(Protocol):
    """Put a stage's produced content and get a reference; resolve a reference back to content."""

    def put(
        self, correlation_id: str, stage: str, content: str, *, name: str = "output"
    ) -> str: ...
    def get(self, ref: str) -> str | None: ...
    def list(self, correlation_id: str) -> list[str]: ...


def build_artifact_store(
    config: dict[str, Any] | None,
    *,
    workspace_root: Path,
    database_url: str,
) -> ArtifactStore:
    """Construct the workspace-configured artifact store from ``storage.artifacts``.

    Default backend is ``database`` (zero-config, on the same SQLite/Postgres). ``filesystem``
    writes under ``{workspace}/.swarmkit/artifacts``; ``s3`` needs the optional ``boto3`` dep.
    """
    cfg = config or {}
    backend = str(cfg.get("backend", "database"))
    if backend == "filesystem":
        from swarmkit_runtime.artifacts._backends import FileSystemArtifactStore  # noqa: PLC0415

        root = Path(cfg.get("path") or (workspace_root / ".swarmkit" / "artifacts"))
        return FileSystemArtifactStore(root)
    if backend == "s3":
        from swarmkit_runtime.artifacts._backends import S3ArtifactStore  # noqa: PLC0415

        return S3ArtifactStore(
            bucket=str(cfg["bucket"]), prefix=str(cfg.get("prefix", "artifacts"))
        )
    from swarmkit_runtime.artifacts._backends import DatabaseArtifactStore  # noqa: PLC0415

    return DatabaseArtifactStore.from_url(str(cfg.get("database_url") or database_url))


__all__ = ["ArtifactStore", "artifact_ref", "build_artifact_store"]
