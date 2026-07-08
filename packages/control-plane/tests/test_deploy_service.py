"""Unit tests for DeployService — governed deploy logic, exercised directly (no HTTP).

Proves the ordering invariant (intent recorded only after a successful push), the Mode A / Mode B
dispatch, the schema-compat gate, and the error taxonomy — without a TestClient.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_control_plane._aggregation import AggregationStore
from swarmkit_control_plane._artifacts import ArtifactStore
from swarmkit_control_plane._deploy import DeployError
from swarmkit_control_plane._models import Instance
from swarmkit_control_plane._registry import SqliteRegistry
from swarmkit_control_plane._service import (
    BadRequestError,
    ConflictError,
    DeployService,
    NotFoundError,
    UpstreamError,
)


def _service(
    tmp_path: Path, *, deploy: Any = None
) -> tuple[DeployService, SqliteRegistry, ArtifactStore, list[Any]]:
    db = tmp_path / "registry.sqlite"
    registry = SqliteRegistry(db)
    artifacts = ArtifactStore(db)
    agg = AggregationStore(db)
    artifacts.register_version("topology", "hello", content={"nodes": ["root"]})  # v1

    calls: list[Any] = []
    if deploy is None:

        async def deploy(
            endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **_sig: Any
        ) -> dict[str, Any]:
            calls.append((endpoint, kind, aid, content))
            return {"deployed": aid}

    return DeployService(registry, artifacts, agg, deploy), registry, artifacts, calls


def _enroll(
    registry: SqliteRegistry, connection: str = "direct", *, schema_version: str = ""
) -> str:
    inst = Instance(
        id=f"i-{connection}",
        name="x",
        endpoint="http://serve:8000",
        connection=connection,  # type: ignore[arg-type]
        tier="admin",
        token_ref="env:X_TOKEN",  # legacy deploy credential (no membership in these unit tests)
        schema_version=schema_version,
    )
    registry.add(inst)
    return inst.id


@pytest.mark.asyncio
async def test_mode_a_pushes_records_intent_and_audits(tmp_path: Path) -> None:
    svc, registry, artifacts, calls = _service(tmp_path)
    iid = _enroll(registry, "direct")
    out = await svc.deploy(
        instance_id=iid, kind="topology", artifact_id="hello", version="v1", by="alice"
    )
    assert out["status"] == "ok" and out["mode"] == "direct"
    assert calls == [("http://serve:8000", "topology", "hello", {"nodes": ["root"]})]
    assert artifacts.list_deployments(iid)[0]["version"] == "v1"


@pytest.mark.asyncio
async def test_mode_b_enqueues_and_does_not_push(tmp_path: Path) -> None:
    svc, registry, _, calls = _service(tmp_path)
    iid = _enroll(registry, "poll")
    out = await svc.deploy(instance_id=iid, kind="topology", artifact_id="hello", version="v1")
    assert out["mode"] == "poll" and out["command_id"]
    assert calls == []  # no direct push for poll instances
    cmds = registry.list_commands(iid)
    assert cmds[0].verb == "deploy" and cmds[0].args["body"] == {"nodes": ["root"]}


@pytest.mark.asyncio
async def test_failed_push_records_no_deployment(tmp_path: Path) -> None:
    """The ordering invariant: a failed Mode-A push leaves no phantom 'deployed v1' record."""

    async def boom(
        endpoint: str, token_ref: str, kind: str, aid: str, content: Any, **_sig: Any
    ) -> dict[str, Any]:
        raise DeployError("connection refused")

    svc, registry, artifacts, _ = _service(tmp_path, deploy=boom)
    iid = _enroll(registry, "direct")
    with pytest.raises(UpstreamError):
        await svc.deploy(instance_id=iid, kind="topology", artifact_id="hello", version="v1")
    assert artifacts.list_deployments(iid) == []


@pytest.mark.asyncio
async def test_missing_instance_is_not_found(tmp_path: Path) -> None:
    svc, _, _, _ = _service(tmp_path)
    with pytest.raises(NotFoundError):
        await svc.deploy(instance_id="ghost", kind="topology", artifact_id="hello", version="v1")


@pytest.mark.asyncio
async def test_undeployable_kind_is_bad_request(tmp_path: Path) -> None:
    svc, registry, _, _ = _service(tmp_path)
    iid = _enroll(registry, "poll")
    with pytest.raises(BadRequestError):
        await svc.deploy(instance_id=iid, kind="workspace", artifact_id="w", version="v1")


@pytest.mark.asyncio
async def test_unknown_version_is_not_found(tmp_path: Path) -> None:
    svc, registry, _, _ = _service(tmp_path)
    iid = _enroll(registry, "poll")
    with pytest.raises(NotFoundError):
        await svc.deploy(instance_id=iid, kind="topology", artifact_id="hello", version="v9")


@pytest.mark.asyncio
async def test_schema_incompatible_is_conflict(tmp_path: Path) -> None:
    svc, registry, artifacts, _ = _service(tmp_path)
    # Publish a v2 built for schema 2.0; the instance reports 1.4 → different major → incompatible.
    artifacts.register_version(
        "topology", "hello", content={"nodes": ["root", "b"]}, schema_version="2.0.0"
    )
    iid = _enroll(registry, "direct", schema_version="1.4.0")
    with pytest.raises(ConflictError):
        await svc.deploy(instance_id=iid, kind="topology", artifact_id="hello", version="v2")
