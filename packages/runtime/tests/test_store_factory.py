"""Tests for store backend factory.

Covers:
- Default to SQLite
- Env var override (SWARMKIT_STORE_BACKEND)
- Workspace config override
- Postgres fallback to SQLite (not yet implemented)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from swarmkit_runtime.persistence._factory import StoreConfigError, _resolve_backend, create_store
from swarmkit_runtime.persistence._store import SqliteStore, make_engine, normalize_url


def test_default_sqlite(tmp_path: Path) -> None:
    store = create_store(tmp_path)
    assert isinstance(store, SqliteStore)


def test_env_var_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "sqlite")
    store = create_store(tmp_path)
    assert isinstance(store, SqliteStore)


def test_env_var_postgres_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # postgres + a URL now genuinely selects postgres (no more silent sqlite fallback).
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    assert _resolve_backend(tmp_path) == ("postgres", "postgresql://localhost/test", "env")


def test_postgres_without_a_url_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """It used to warn and degrade to sqlite. A storage backend must not do that: the run then
    writes to a different database than the one configured, and serve and the orchestrator split
    without either process noticing."""
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SWARMKIT_STORE_URL", raising=False)
    with pytest.raises(StoreConfigError, match="no URL"):
        _resolve_backend(tmp_path)
    with pytest.raises(StoreConfigError):
        create_store(tmp_path)


def test_workspace_config_sqlite(tmp_path: Path) -> None:
    ws = MagicMock()
    ws.storage.runtime.backend = "sqlite"
    ws.storage.runtime.url = ""
    store = create_store(tmp_path, workspace_raw=ws)
    assert isinstance(store, SqliteStore)


def test_workspace_config_postgres_selected(tmp_path: Path) -> None:
    ws = MagicMock()
    ws.storage.runtime.backend = "postgres"
    ws.storage.runtime.url = "postgresql://db/app"
    assert _resolve_backend(tmp_path, ws) == ("postgres", "postgresql://db/app", "workspace.yaml")


def test_workspace_no_storage(tmp_path: Path) -> None:
    ws = MagicMock()
    ws.storage = None
    store = create_store(tmp_path, workspace_raw=ws)
    assert isinstance(store, SqliteStore)


def test_postgres_url_normalized_to_psycopg3() -> None:
    # A bare postgresql:// URL is pointed at psycopg 3, and create_engine is lazy (no connection).
    assert normalize_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("postgres://h/db") == "postgresql+psycopg://h/db"
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"
    engine = make_engine("postgresql://localhost/test")
    assert engine.dialect.name == "postgresql"
    assert engine.url.drivername == "postgresql+psycopg"


@pytest.mark.integration
def test_postgres_store_roundtrip() -> None:
    """The same Store on a real Postgres — runs only when SWARMKIT_TEST_POSTGRES_URL is set
    (deselected by default; guards the dialect end-to-end)."""
    import os  # noqa: PLC0415

    from swarmkit_runtime.persistence._store import Store  # noqa: PLC0415

    url = os.environ.get("SWARMKIT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set SWARMKIT_TEST_POSTGRES_URL to run the Postgres store test")
    store = Store(make_engine(url))
    assert store.engine.dialect.name == "postgresql"
    store.create_job("pg-job-1", "hello", "hi")
    got = store.get_job("pg-job-1")
    assert got is not None and got.topology == "hello" and got.status == "pending"
    store.update_job("pg-job-1", status="completed", output="done")
    assert store.get_job("pg-job-1").status == "completed"  # type: ignore[union-attr]


# ---- the shape production actually passes ------------------------------------------------------
#
# `create_store` is called with `runtime.workspace.raw` — a parsed-YAML Mapping, not a model. The
# resolver used plain getattr, which returns the default for a Mapping, so `storage.runtime` was
# DEAD CONFIG for every workspace: a configured postgres backend silently ran on sqlite, with no
# error and no warning. The tests above missed it because MagicMock answers attribute access for
# anything. These use the real shape.


def test_mapping_workspace_config_is_read(tmp_path: Path) -> None:
    raw = {"storage": {"runtime": {"backend": "postgres", "url": "postgresql://x/y"}}}
    assert _resolve_backend(tmp_path, raw) == ("postgres", "postgresql://x/y", "workspace.yaml")


def test_mapping_without_storage_defaults_to_sqlite(tmp_path: Path) -> None:
    assert _resolve_backend(tmp_path, {}) == ("sqlite", "", "default")
    assert _resolve_backend(tmp_path, {"storage": {}}) == ("sqlite", "", "default")


def test_env_var_beats_the_workspace_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "sqlite")
    raw = {"storage": {"runtime": {"backend": "postgres", "url": "postgresql://x/y"}}}
    backend, _url, source = _resolve_backend(tmp_path, raw)
    assert (backend, source) == ("sqlite", "env")


def test_source_names_where_the_answer_came_from(tmp_path: Path) -> None:
    """So a surprising backend is visible in the startup log rather than inferred."""
    raw = {"storage": {"runtime": {"backend": "sqlite"}}}
    assert _resolve_backend(tmp_path, raw)[2] == "workspace.yaml"
    assert _resolve_backend(tmp_path, None)[2] == "default"


def test_typed_model_with_an_enum_backend_is_read(tmp_path: Path) -> None:
    """The actual root cause on the serve path, which is subtler than the Mapping problem.

    `ResolvedWorkspace.raw` is a typed `SwarmKitWorkspace`, and the schema models `backend` as an
    ENUM. `Backend2.postgres` is not a str: `== "postgres"` is False and `str()` yields
    "Backend2.postgres". So the comparison never matched and every workspace ran on sqlite however
    it was configured — no error, no warning, and the target database stayed empty.
    """
    from swarmkit_schema.models.workspace import Backend2  # noqa: PLC0415

    assert not isinstance(Backend2.postgres, str)  # the trap, pinned

    class _Runtime:
        backend = Backend2.postgres
        url = "postgresql://db/app"

    class _Storage:
        runtime = _Runtime()

    class _Workspace:
        storage = _Storage()

    assert _resolve_backend(tmp_path, _Workspace()) == (
        "postgres",
        "postgresql://db/app",
        "workspace.yaml",
    )
