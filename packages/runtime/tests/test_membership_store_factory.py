"""create_membership_store honours the instance's configured storage backend (design 19 Q4).

Default sqlite lives at a dedicated ``.swarmkit/fleet.sqlite``; a configured backend (env or
workspace.yaml ``storage.runtime``) points the fleet store at the same DB as the rest of the
instance — so on a Postgres deployment fleet data isn't stranded in a local sqlite file. Postgres
needs a live server, so the routing is exercised with a sqlite URL through the same code path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from swarmkit_runtime.fleet import create_membership_store


def test_default_is_dedicated_fleet_sqlite(tmp_path: Path) -> None:
    store = create_membership_store(tmp_path)
    store.create_enrollment_token("monitor")
    # the default backend writes the dedicated fleet.sqlite next to the workspace, not store.sqlite.
    assert (tmp_path / ".swarmkit" / "fleet.sqlite").exists()
    assert not (tmp_path / ".swarmkit" / "store.sqlite").exists()


def test_env_backend_url_routes_to_that_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A configured backend + URL points the fleet store at that DB (routing exercised with a sqlite
    # URL through the postgres branch — Postgres would be identical, just a different dialect).
    db = tmp_path / "shared.sqlite"
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "postgres")
    monkeypatch.setenv("SWARMKIT_STORE_URL", f"sqlite:///{db}")

    store = create_membership_store(tmp_path)
    store.create_enrollment_token("manage")

    # the token landed in the configured DB, NOT in a dedicated .swarmkit/fleet.sqlite.
    assert db.exists()
    assert not (tmp_path / ".swarmkit" / "fleet.sqlite").exists()
    tables = inspect(create_engine(f"sqlite:///{db}")).get_table_names()
    assert "fleet_enrollment_tokens" in tables and "fleet_memberships" in tables


def test_workspace_yaml_storage_config_is_honoured(tmp_path: Path) -> None:
    # The CLI has no resolved workspace model, so the factory reads storage.runtime from
    # workspace.yaml directly — the fleet store follows the workspace-configured backend.
    db = tmp_path / "ws-configured.sqlite"
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: Workspace\n"
        "storage:\n"
        "  runtime:\n"
        "    backend: postgres\n"
        f"    url: sqlite:///{db}\n",
        encoding="utf-8",
    )
    store = create_membership_store(tmp_path)
    store.create_enrollment_token("monitor")
    assert db.exists()
    assert not (tmp_path / ".swarmkit" / "fleet.sqlite").exists()


def test_cli_and_serve_share_the_configured_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A token minted via the factory (as the CLI does) is consumable via a store built the same way
    # (as serve does) when both resolve the same configured backend — one location, no divergence.
    db = tmp_path / "shared.sqlite"
    monkeypatch.setenv("SWARMKIT_STORE_BACKEND", "postgres")
    monkeypatch.setenv("SWARMKIT_STORE_URL", f"sqlite:///{db}")

    cli_store = create_membership_store(tmp_path)
    token = cli_store.create_enrollment_token("manage")

    serve_store = create_membership_store(tmp_path)  # a fresh handle, same backend
    assert serve_store.consume_enrollment_token(token) == "manage"  # found it
    assert serve_store.consume_enrollment_token(token) is None  # single-use across the shared DB
