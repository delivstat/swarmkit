"""Unit tests for the runtime server service layer — JobService + ArtifactService (no HTTP).

JobService.resolve_topology is the logic that used to be duplicated across /run and /hooks; the
ArtifactService cases drive real file writes against a throwaway copy of the example workspace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import copy_workspace
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.server import _services
from swarmkit_runtime.server._config import ServerCfg
from swarmkit_runtime.server._jobs import JobStore
from swarmkit_runtime.server._services import (
    ArtifactService,
    BusyError,
    JobService,
    NotFoundError,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


# ---- JobService.resolve_topology (the de-duplicated /run + /hooks logic) -----


class _FakeRT:
    def __init__(self, topologies: dict[str, object]) -> None:
        self.workspace = type("_WS", (), {"topologies": topologies})()


class _FakeCanary:
    def __init__(self, routes: dict[str, str]) -> None:
        self._routes = routes

    def has_route(self, name: str) -> bool:
        return name in self._routes

    def select(self, name: str) -> str:
        return self._routes[name]


def test_resolve_plain_topology() -> None:
    svc = JobService(JobStore())
    rt = _FakeRT({"hello": object()})
    assert svc.resolve_topology(rt, None, "hello") == ("hello", None)  # type: ignore[arg-type]


def test_resolve_canary_selects_versioned_name() -> None:
    svc = JobService(JobStore())
    rt = _FakeRT({"hello@v2": object()})
    canary = _FakeCanary({"hello": "v2"})
    assert svc.resolve_topology(rt, canary, "hello") == ("hello@v2", "v2")  # type: ignore[arg-type]


def test_resolve_canary_falls_back_to_bare_name() -> None:
    # A canary route selects v2 but only the bare topology exists → run the bare one, no version.
    svc = JobService(JobStore())
    rt = _FakeRT({"hello": object()})
    canary = _FakeCanary({"hello": "v2"})
    assert svc.resolve_topology(rt, canary, "hello") == ("hello", None)  # type: ignore[arg-type]


def test_resolve_unknown_topology_raises_not_found() -> None:
    svc = JobService(JobStore())
    rt = _FakeRT({"hello": object()})
    with pytest.raises(NotFoundError) as exc:
        svc.resolve_topology(rt, None, "ghost")  # type: ignore[arg-type]
    assert "ghost" in str(exc.value) and "hello" in str(exc.value)  # lists what's available


class _FakeStore:
    def __init__(self) -> None:
        self.created: list[Any] = []
        self.correlations: list[str | None] = []
        self.labels: list[dict[str, str]] = []
        self.parents: list[str | None] = []
        self.updated: list[Any] = []

    def create_job(
        self,
        job_id: str,
        topology: str,
        user_input: str,
        correlation_id: str | None = None,
        source: str | None = None,
        labels: dict[str, str] | None = None,
        # Completed rather than absorbed by **kwargs: a double that silently swallows a new
        # argument is how a field the caller passes stops reaching the store with every test green.
        parent_job_id: str | None = None,
    ) -> None:
        # Correlation and labels recorded, so a test can assert the HTTP surface passes them —
        # `RunRequest` could not carry either until now, so a run started over the API was
        # uncorrelated no matter what the caller sent.
        self.created.append((job_id, topology, user_input))
        self.correlations.append(correlation_id)
        self.labels.append(dict(labels or {}))
        self.parents.append(parent_job_id)

    def update_job(self, job_id: str, **kw: Any) -> None:
        self.updated.append((job_id, kw))


@pytest.mark.asyncio
async def test_start_creates_persists_and_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """start() resolves, creates + persists the job, and hands off to _start_job — without this
    test launching a real background run (that path is covered e2e by test_server)."""
    launched: list[Any] = []
    monkeypatch.setattr(_services, "_start_job", lambda *a, **k: launched.append((a, k)))

    store = _FakeStore()
    svc = JobService(JobStore())
    rt = _FakeRT({"hello@v2": object()})
    job = await svc.start(
        rt=rt,  # type: ignore[arg-type]
        canary=_FakeCanary({"hello": "v2"}),  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        cfg=ServerCfg(),
        semaphore=None,
        topology_name="hello",
        user_input="hi",
        max_steps=7,
    )
    assert job.topology == "hello@v2" and job.version == "v2"
    assert store.created == [(job.id, "hello@v2", "hi")]
    assert store.updated == [(job.id, {"version": "v2"})]
    assert launched  # delegated to _start_job (the background launcher)


@pytest.mark.asyncio
async def test_start_rejects_when_at_capacity() -> None:
    import asyncio  # noqa: PLC0415

    sem = asyncio.Semaphore(1)
    await sem.acquire()  # all slots taken
    svc = JobService(JobStore())
    with pytest.raises(BusyError):
        await svc.start(
            rt=_FakeRT({"hello": object()}),  # type: ignore[arg-type]
            canary=None,
            store=None,
            cfg=ServerCfg(),
            semaphore=sem,
            topology_name="hello",
            user_input="hi",
            max_steps=1,
        )


# ---- ArtifactService (real file ops on a throwaway workspace copy) -----------


@pytest.fixture()
def artifacts(tmp_path: Path) -> ArtifactService:
    ws = tmp_path / "workspace"
    copy_workspace(EXAMPLE_WS, ws)
    return ArtifactService(ws)


_MIN_TOPOLOGY = """\
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: {name}
  version: 0.1.0
agents:
  root:
    id: root
    role: root
    prompt:
      system: Say hello.
"""


def test_find_and_read_yaml(artifacts: ArtifactService) -> None:
    assert artifacts.find_file("topology", "hello") is not None
    assert "hello" in artifacts.read_yaml("topology", "hello")
    with pytest.raises(NotFoundError):
        artifacts.read_yaml("topology", "ghost")


def test_validate_workspace_ok(artifacts: ArtifactService) -> None:
    result = artifacts.validate_workspace()
    assert result["valid"] is True and "hello" in result["topologies"]


def test_put_yaml_dry_run_writes_nothing(artifacts: ArtifactService) -> None:
    result, new_rt = artifacts.put_yaml(
        "topology", "hello2", _MIN_TOPOLOGY.format(name="hello2"), dry_run=True, parse_check=True
    )
    assert result["valid"] is True
    assert new_rt is None  # dry-run never reloads
    assert artifacts.find_file("topology", "hello2") is None  # nor writes


def test_put_yaml_parse_error_short_circuits(artifacts: ArtifactService) -> None:
    result, new_rt = artifacts.put_yaml(
        "topology", "bad", "key: [unterminated", dry_run=False, parse_check=True
    )
    assert result["valid"] is False and result["errors"][0]["code"] == "yaml.parse"
    assert new_rt is None
    assert artifacts.find_file("topology", "bad") is None  # nothing written on a parse error


def test_create_then_delete_roundtrip(artifacts: ArtifactService) -> None:
    result, new_rt = artifacts.create_from_yaml("topology", _MIN_TOPOLOGY.format(name="fresh"))
    assert result["valid"] is True and new_rt is not None
    assert "fresh" in result["topologies"]

    dup, _ = artifacts.create_from_yaml("topology", _MIN_TOPOLOGY.format(name="fresh"))
    assert dup["valid"] is False and dup["errors"][0]["code"] == "exists"

    deleted, rt2 = artifacts.delete("topology", "fresh")
    assert deleted == {"deleted": True, "id": "fresh"} and rt2 is not None
    assert artifacts.find_file("topology", "fresh") is None


def test_create_missing_name_is_rejected(artifacts: ArtifactService) -> None:
    result, _ = artifacts.create_from_yaml("topology", "apiVersion: swarmkit/v1\nkind: Topology\n")
    assert result["valid"] is False and result["errors"][0]["code"] == "missing.name"


def test_delete_missing_raises_not_found(artifacts: ArtifactService) -> None:
    with pytest.raises(NotFoundError):
        artifacts.delete("topology", "ghost")


def test_detail_projections(artifacts: ArtifactService) -> None:
    rt = WorkspaceRuntime.from_workspace_path(artifacts._ws)
    detail = artifacts.topology_detail(rt, "hello")
    assert detail["id"] == "hello" and detail["resolved"]["id"]
    with pytest.raises(NotFoundError):
        artifacts.topology_detail(rt, "ghost")


@pytest.mark.asyncio
async def test_start_records_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """`parent_job_id` reaches the store. The column, the store argument and the merged read all
    shipped without a way for any caller to set the field, so every chain in the database was NULL —
    the mirror image of a field nothing can read."""
    monkeypatch.setattr(_services, "_start_job", lambda *a, **k: None)
    store = _FakeStore()

    await JobService(JobStore()).start(
        rt=_FakeRT({"hello": object()}),  # type: ignore[arg-type]
        canary=None,
        store=store,  # type: ignore[arg-type]
        cfg=ServerCfg(),
        semaphore=None,
        topology_name="hello",
        user_input="redo it",
        max_steps=7,
        correlation_id="WMS-35",
        parent_job_id="job-1",
    )

    assert store.parents == ["job-1"]
    assert store.correlations == ["WMS-35"], "and the two are separate facts"
