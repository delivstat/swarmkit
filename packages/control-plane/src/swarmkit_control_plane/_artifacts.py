"""ArtifactStore — central versioned registry for SwarmKit artifacts + drift detection.

Versions topologies / skills / archetypes / workspace / triggers with provenance and a
``content_hash`` (design details/control-plane/15-artifact-registry.md). Registering identical
content is idempotent (returns the existing latest); changed content is a new version, never a
silent overwrite. Tracks the registry-*intended* version per instance (a deployment) and the
instance's *reported* actual version, and computes drift between them.

SQLAlchemy Core over SQLite (default) or Postgres (design/details/postgres-backend.md); the
design's git-backed content store is a later swap. The governed/audited/human-gated push to
instances, the schema-compatibility gate, and the UI surface are separate slices.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, RowMapping

from swarmkit_control_plane._store_base import Store, upsert
from swarmkit_control_plane._tables import artifact_versions, deployments, reported_artifacts

KINDS = ("topology", "skill", "archetype", "workspace", "trigger")


def content_hash(content: Any) -> str:
    """Stable SHA-256 of artifact content (dicts hashed canonically, sorted keys)."""
    text = (
        json.dumps(content, sort_keys=True, separators=(",", ":"))
        if isinstance(content, dict)
        else str(content)
    )
    return hashlib.sha256(text.encode()).hexdigest()


class ArtifactStore(Store):
    """Thread-safe store for versioned artifacts, deployments, and drift."""

    def register_version(
        self,
        kind: str,
        id: str,
        *,
        content: Any,
        authored_by: str = "",
        schema_version: str = "",
        version: str | None = None,
    ) -> dict[str, Any]:
        """Register a version. Idempotent: identical content to the latest returns that version."""
        chash = content_hash(content)
        now = datetime.now(UTC).isoformat()
        with self._lock, self._engine.begin() as conn:
            rows = (
                conn.execute(
                    select(
                        artifact_versions.c.version,
                        artifact_versions.c.content_hash,
                        artifact_versions.c.seq,
                    )
                    .where(artifact_versions.c.kind == kind, artifact_versions.c.id == id)
                    .order_by(artifact_versions.c.seq.desc())
                )
                .mappings()
                .all()
            )
            if rows and rows[0]["content_hash"] == chash:
                return self._get_version(conn, kind, id, rows[0]["version"])  # no-op
            seq = (rows[0]["seq"] + 1) if rows else 1
            ver = version or f"v{seq}"
            conn.execute(
                artifact_versions.insert().values(
                    kind=kind,
                    id=id,
                    version=ver,
                    content_hash=chash,
                    content=json.dumps(content) if isinstance(content, dict) else str(content),
                    authored_by=authored_by,
                    schema_version=schema_version,
                    created_at=now,
                    seq=seq,
                )
            )
            return self._get_version(conn, kind, id, ver)

    def _get_version(self, conn: Connection, kind: str, id: str, version: str) -> dict[str, Any]:
        row = (
            conn.execute(
                select(artifact_versions).where(
                    artifact_versions.c.kind == kind,
                    artifact_versions.c.id == id,
                    artifact_versions.c.version == version,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise KeyError(f"{kind}/{id}@{version}")
        return self._row_to_version(row)

    def _row_to_version(self, row: RowMapping, *, with_content: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": row["kind"],
            "id": row["id"],
            "version": row["version"],
            "content_hash": row["content_hash"],
            "authored_by": row["authored_by"],
            "schema_version": row["schema_version"],
            "created_at": row["created_at"],
        }
        if with_content:
            raw = row["content"]
            try:
                out["content"] = json.loads(raw)
            except (ValueError, TypeError):
                out["content"] = raw
        return out

    def get_version(self, kind: str, id: str, version: str) -> dict[str, Any] | None:
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(artifact_versions).where(
                        artifact_versions.c.kind == kind,
                        artifact_versions.c.id == id,
                        artifact_versions.c.version == version,
                    )
                )
                .mappings()
                .first()
            )
        return self._row_to_version(row) if row else None

    def list_versions(self, kind: str, id: str) -> list[dict[str, Any]]:
        with self._lock, self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(artifact_versions)
                    .where(artifact_versions.c.kind == kind, artifact_versions.c.id == id)
                    .order_by(artifact_versions.c.seq.desc())
                )
                .mappings()
                .all()
            )
        return [self._row_to_version(r, with_content=False) for r in rows]

    def list_artifacts(self) -> list[dict[str, Any]]:
        """One row per (kind, id): latest version + total version count."""
        av = artifact_versions.c
        with self._lock, self._engine.connect() as conn:
            groups = (
                conn.execute(
                    select(
                        av.kind,
                        av.id,
                        func.count().label("versions"),
                        func.max(av.seq).label("max_seq"),
                    )
                    .group_by(av.kind, av.id)
                    .order_by(av.kind, av.id)
                )
                .mappings()
                .all()
            )
            out = []
            for r in groups:
                latest = (
                    conn.execute(
                        select(av.version, av.content_hash).where(
                            av.kind == r["kind"], av.id == r["id"], av.seq == r["max_seq"]
                        )
                    )
                    .mappings()
                    .first()
                )
                out.append(
                    {
                        "kind": r["kind"],
                        "id": r["id"],
                        "versions": r["versions"],
                        "latest_version": latest["version"] if latest else None,
                        "latest_hash": latest["content_hash"] if latest else None,
                    }
                )
        return out

    # --- deployments (registry-intended version per instance) ----------------

    def set_deployment(self, instance_id: str, kind: str, id: str, version: str) -> None:
        with self._lock, self._engine.begin() as conn:
            exists = conn.execute(
                select(artifact_versions.c.version).where(
                    artifact_versions.c.kind == kind,
                    artifact_versions.c.id == id,
                    artifact_versions.c.version == version,
                )
            ).first()
            if exists is None:
                raise KeyError(f"no such version {kind}/{id}@{version}")
            updated_at = datetime.now(UTC).isoformat()
            conn.execute(
                upsert(
                    self._engine,
                    deployments,
                    {
                        "instance_id": instance_id,
                        "kind": kind,
                        "id": id,
                        "version": version,
                        "updated_at": updated_at,
                    },
                    index_elements=["instance_id", "kind", "id"],
                    set_={"version": version, "updated_at": updated_at},
                )
            )

    def list_deployments(self, instance_id: str) -> list[dict[str, Any]]:
        d = deployments.c
        with self._lock, self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(d.kind, d.id, d.version, d.updated_at)
                    .where(d.instance_id == instance_id)
                    .order_by(d.kind, d.id)
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    # --- reported actual + drift ---------------------------------------------

    def report(self, instance_id: str, records: list[dict[str, Any]]) -> int:
        """Record an instance's actual active artifact versions (for drift)."""
        now = datetime.now(UTC).isoformat()
        n = 0
        with self._lock, self._engine.begin() as conn:
            for rec in records:
                kind, rid = rec.get("kind"), rec.get("id")
                if kind not in KINDS or not rid:
                    continue
                version = str(rec.get("version", ""))
                chash = str(rec.get("content_hash", ""))
                conn.execute(
                    upsert(
                        self._engine,
                        reported_artifacts,
                        {
                            "instance_id": instance_id,
                            "kind": kind,
                            "id": rid,
                            "version": version,
                            "content_hash": chash,
                            "reported_at": now,
                        },
                        index_elements=["instance_id", "kind", "id"],
                        set_={"version": version, "content_hash": chash, "reported_at": now},
                    )
                )
                n += 1
        return n

    def drift(self, instance_id: str) -> list[dict[str, Any]]:
        """Compare each intended deployment against the instance's reported actual."""
        av, ra = artifact_versions.c, reported_artifacts.c
        with self._lock, self._engine.connect() as conn:
            intended = (
                conn.execute(
                    select(deployments.c.kind, deployments.c.id, deployments.c.version).where(
                        deployments.c.instance_id == instance_id
                    )
                )
                .mappings()
                .all()
            )
            out = []
            for d in intended:
                want = (
                    conn.execute(
                        select(av.content_hash).where(
                            av.kind == d["kind"], av.id == d["id"], av.version == d["version"]
                        )
                    )
                    .mappings()
                    .first()
                )
                got = (
                    conn.execute(
                        select(ra.version, ra.content_hash).where(
                            ra.instance_id == instance_id, ra.kind == d["kind"], ra.id == d["id"]
                        )
                    )
                    .mappings()
                    .first()
                )
                if got is None:
                    status = "missing"  # intended but the instance hasn't reported it
                elif got["version"] == d["version"] and (
                    not want
                    or not got["content_hash"]
                    or got["content_hash"] == want["content_hash"]
                ):
                    status = "ok"
                else:
                    status = "drift"
                out.append(
                    {
                        "kind": d["kind"],
                        "id": d["id"],
                        "intended_version": d["version"],
                        "actual_version": got["version"] if got else None,
                        "status": status,
                    }
                )
        return out
