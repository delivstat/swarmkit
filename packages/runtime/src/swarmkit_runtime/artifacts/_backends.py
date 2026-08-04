"""ArtifactStore backends: database (default), filesystem, and s3 (optional dep).

All address content by the deterministic ``<correlation_id>/<stage>/<name>`` reference from
``_store.artifact_ref``. The database backend rides the persistence engine (zero-config); filesystem
writes files under a root; s3 lazy-imports ``boto3`` so the runtime carries no hard cloud dep.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Engine, MetaData, Table, Text, insert, select

from swarmkit_runtime.artifacts._store import artifact_ref
from swarmkit_runtime.persistence._store import create_all_idempotent, make_engine

metadata = MetaData()

pipeline_artifacts = Table(
    "pipeline_artifacts",
    metadata,
    Column("ref", Text, primary_key=True),
    Column("correlation_id", Text, nullable=False),
    Column("stage", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)


class DatabaseArtifactStore:
    """Artifacts as rows on the persistence engine — the zero-config default."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        create_all_idempotent(metadata, engine)

    @classmethod
    def from_url(cls, url: str) -> DatabaseArtifactStore:
        return cls(make_engine(url))

    def put(self, correlation_id: str, stage: str, content: str, *, name: str = "output") -> str:
        ref = artifact_ref(correlation_id, stage, name)
        with self._engine.begin() as conn:
            conn.execute(
                pipeline_artifacts.delete().where(pipeline_artifacts.c.ref == ref)
            )  # overwrite on retry
            conn.execute(
                insert(pipeline_artifacts).values(
                    ref=ref,
                    correlation_id=correlation_id,
                    stage=stage,
                    name=name,
                    content=content,
                    created_at=datetime.now(tz=UTC).isoformat(),
                )
            )
        return ref

    def get(self, ref: str) -> str | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(pipeline_artifacts.c.content).where(pipeline_artifacts.c.ref == ref)
            ).first()
        return row[0] if row else None

    def list(self, correlation_id: str) -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(pipeline_artifacts.c.ref)
                .where(pipeline_artifacts.c.correlation_id == correlation_id)
                .order_by(pipeline_artifacts.c.ref)
            ).scalars()
            return list(rows)


class FileSystemArtifactStore:
    """Artifacts as files under ``{root}/{correlation_id}/{stage}/{name}``."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _path(self, ref: str) -> Path:
        return self._root / ref

    def put(self, correlation_id: str, stage: str, content: str, *, name: str = "output") -> str:
        ref = artifact_ref(correlation_id, stage, name)
        path = self._path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ref

    def get(self, ref: str) -> str | None:
        path = self._path(ref)
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def list(self, correlation_id: str) -> list[str]:
        base = self._root / correlation_id
        if not base.is_dir():
            return []
        return sorted(str(p.relative_to(self._root)) for p in base.rglob("*") if p.is_file())


class S3ArtifactStore:
    """Artifacts in an S3-compatible bucket. Needs the optional ``boto3`` dependency."""

    def __init__(self, *, bucket: str, prefix: str = "artifacts") -> None:
        try:
            import boto3  # type: ignore[import-not-found]  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "storage.artifacts.backend=s3 requires boto3 — install swarmkit-runtime[s3]"
            ) from exc
        self._s3: Any = boto3.client("s3")
        self._bucket = bucket
        self._prefix = prefix.strip("/")

    def _key(self, ref: str) -> str:
        return f"{self._prefix}/{ref}"

    def put(self, correlation_id: str, stage: str, content: str, *, name: str = "output") -> str:
        ref = artifact_ref(correlation_id, stage, name)
        self._s3.put_object(Bucket=self._bucket, Key=self._key(ref), Body=content.encode("utf-8"))
        return ref

    def get(self, ref: str) -> str | None:  # pragma: no cover - needs a live bucket
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._key(ref))
        except self._s3.exceptions.NoSuchKey:
            return None
        return str(obj["Body"].read().decode("utf-8"))

    def list(self, correlation_id: str) -> list[str]:  # pragma: no cover - needs a live bucket
        resp = self._s3.list_objects_v2(
            Bucket=self._bucket, Prefix=f"{self._prefix}/{correlation_id}/"
        )
        return [o["Key"].removeprefix(f"{self._prefix}/") for o in resp.get("Contents", [])]


__all__ = [
    "DatabaseArtifactStore",
    "FileSystemArtifactStore",
    "S3ArtifactStore",
    "pipeline_artifacts",
]
