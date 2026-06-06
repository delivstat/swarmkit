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
from swarmkit_runtime.persistence._factory import create_store
from swarmkit_runtime.persistence._sqlite import SqliteStore


def test_default_sqlite(tmp_path: Path) -> None:
    store = create_store(tmp_path)
    assert isinstance(store, SqliteStore)


def test_env_var_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "sqlite")
    store = create_store(tmp_path)
    assert isinstance(store, SqliteStore)


def test_env_var_postgres_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    store = create_store(tmp_path)
    assert isinstance(store, SqliteStore)


def test_env_var_postgres_no_url_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SWARMKIT_STORE_URL", raising=False)
    store = create_store(tmp_path)
    assert isinstance(store, SqliteStore)


def test_workspace_config_sqlite(tmp_path: Path) -> None:
    ws = MagicMock()
    ws.storage.runtime.backend = "sqlite"
    ws.storage.runtime.url = ""
    store = create_store(tmp_path, workspace_raw=ws)
    assert isinstance(store, SqliteStore)


def test_workspace_config_postgres_fallback(tmp_path: Path) -> None:
    ws = MagicMock()
    ws.storage.runtime.backend = "postgres"
    ws.storage.runtime.url = "postgresql://localhost/test"
    store = create_store(tmp_path, workspace_raw=ws)
    assert isinstance(store, SqliteStore)


def test_workspace_no_storage(tmp_path: Path) -> None:
    ws = MagicMock()
    ws.storage = None
    store = create_store(tmp_path, workspace_raw=ws)
    assert isinstance(store, SqliteStore)
