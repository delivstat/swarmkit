"""Pipeline artifact storage — where stage outputs + inter-stage payloads live
(design/details/bundled-pipeline-orchestrator.md §6). A workspace-configured, pluggable store; the
runtime writes/resolves content, the orchestrator threads only references."""

from __future__ import annotations

from swarmkit_runtime.artifacts._backends import (
    DatabaseArtifactStore,
    FileSystemArtifactStore,
    S3ArtifactStore,
)
from swarmkit_runtime.artifacts._store import ArtifactStore, artifact_ref, build_artifact_store

__all__ = [
    "ArtifactStore",
    "DatabaseArtifactStore",
    "FileSystemArtifactStore",
    "S3ArtifactStore",
    "artifact_ref",
    "build_artifact_store",
]
