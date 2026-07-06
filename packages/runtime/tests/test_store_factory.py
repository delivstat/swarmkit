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
from swarmkit_runtime.persistence._factory import _resolve_backend, create_store
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
    assert _resolve_backend(tmp_path) == ("postgres", "postgresql://localhost/test")


def test_env_var_postgres_no_url_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SWARMKIT_STORE_URL", raising=False)
    assert _resolve_backend(tmp_path) == ("sqlite", "")  # no URL → fall back
    assert isinstance(create_store(tmp_path), SqliteStore)


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
    assert _resolve_backend(tmp_path, ws) == ("postgres", "postgresql://db/app")


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
