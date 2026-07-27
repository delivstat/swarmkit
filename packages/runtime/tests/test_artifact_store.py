"""ArtifactStore backends (design/details/bundled-pipeline-orchestrator.md §6): put→ref→get round
trips + list, identical across the database (default) and filesystem backends; the factory picks a
backend from `storage.artifacts`."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from swarmkit_runtime.artifacts import (
    DatabaseArtifactStore,
    FileSystemArtifactStore,
    artifact_ref,
    build_artifact_store,
)


def _db() -> DatabaseArtifactStore:
    return DatabaseArtifactStore(create_engine("sqlite:///:memory:"))


def test_ref_is_deterministic() -> None:
    assert artifact_ref("c1", "build") == "c1/build/output"
    assert artifact_ref("c1", "build", "diff") == "c1/build/diff"


@pytest.mark.parametrize("kind", ["db", "fs"])
def test_put_get_list_roundtrip(kind: str, tmp_path: Path) -> None:
    store = _db() if kind == "db" else FileSystemArtifactStore(tmp_path / "art")
    ref = store.put("c1", "build", "the produced diff")
    assert ref == "c1/build/output"
    assert store.get(ref) == "the produced diff"
    assert store.get("c1/missing/output") is None

    store.put("c1", "review", "review notes", name="notes")
    store.put("c2", "build", "other run")
    assert set(store.list("c1")) == {"c1/build/output", "c1/review/notes"}
    assert store.list("c2") == ["c2/build/output"]


def test_put_overwrites_on_retry(tmp_path: Path) -> None:
    for store in (_db(), FileSystemArtifactStore(tmp_path / "a")):
        store.put("c1", "build", "attempt 1")
        store.put("c1", "build", "attempt 2")  # a retry overwrites, not duplicates
        assert store.get("c1/build/output") == "attempt 2"
        assert store.list("c1") == ["c1/build/output"]


def test_factory_picks_backend(tmp_path: Path) -> None:
    db = build_artifact_store(None, workspace_root=tmp_path, database_url="sqlite:///:memory:")
    assert isinstance(db, DatabaseArtifactStore)
    fs = build_artifact_store(
        {"backend": "filesystem"}, workspace_root=tmp_path, database_url="sqlite:///:memory:"
    )
    assert isinstance(fs, FileSystemArtifactStore)
    fs.put("c1", "s", "x")
    assert (tmp_path / ".swarmkit" / "artifacts" / "c1" / "s" / "output").read_text() == "x"
