"""The single storage service (design/details/storage-service.md).

Each test here is a bug that shipped. The workspace declared Postgres, `swarmkit validate` passed
with no warning, runs succeeded — and the sagas, the audit trail and the governed memory went to a
SQLite file on one laptop. Four separate resolvers disagreed and nothing compared them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.persistence import (
    StorageConfigError,
    StorageService,
    StoreKind,
    storage_for_workspace,
)
from swarmkit_runtime.persistence._service import reset_storage_cache

PG = "postgresql://swarm:hunter2@127.0.0.1:5433/swarmkit"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient storage env, and no service cached from another test's workspace."""
    for var in ("SWARMKIT_STORE_BACKEND", "SWARMKIT_STORE_URL", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    reset_storage_cache()


def _svc(tmp_path: Path, storage: dict[str, Any] | None = None) -> StorageService:
    return StorageService.for_workspace(tmp_path, {"storage": storage} if storage else None)


# ---- the three compounding defects ---------------------------------------------------------


def test_every_store_follows_storage_runtime(tmp_path: Path) -> None:
    """One `storage.runtime` block moves ALL of it. Sagas, artifacts and governed memory each had
    their own hardcoded SQLite path, so declaring Postgres moved only part of the workspace."""
    svc = _svc(tmp_path, {"runtime": {"backend": "postgres", "url": PG}})
    for kind in (StoreKind.RUNTIME, StoreKind.SAGA, StoreKind.ARTIFACTS, StoreKind.MEMORY):
        assert svc.target(kind).backend == "postgres", kind
        assert svc.target(kind).url == PG


def test_audit_follows_runtime_without_its_own_block(tmp_path: Path) -> None:
    """`audit_provider_for_path` never saw the workspace config, so the trail stayed local."""
    svc = _svc(tmp_path, {"runtime": {"backend": "postgres", "url": PG}})
    assert svc.target(StoreKind.AUDIT).backend == "postgres"


def test_audit_block_inherits_the_runtime_url(tmp_path: Path) -> None:
    """Repeating the URL under three keys is what made the config LOOK honoured when it was not."""
    svc = _svc(
        tmp_path,
        {"runtime": {"backend": "postgres", "url": PG}, "audit": {"retention_days": 90}},
    )
    assert svc.target(StoreKind.AUDIT).url == PG


def test_a_url_alone_selects_the_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reported case: `.env` set SWARMKIT_STORE_URL and never SWARMKIT_STORE_BACKEND, so the
    env branch was skipped entirely and a correctly-configured Postgres was ignored."""
    monkeypatch.setenv("SWARMKIT_STORE_URL", PG)
    svc = _svc(tmp_path)
    assert svc.target(StoreKind.RUNTIME).backend == "postgres"
    assert svc.target(StoreKind.RUNTIME).source == "env"


def test_env_vars_in_a_config_url_are_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`url: ${SWARMKIT_STORE_URL}` is the form every deployment doc uses, and nothing in the
    runtime expanded it — SQLAlchemy received those 21 literal characters."""
    monkeypatch.setenv("MY_DB", PG)
    svc = _svc(tmp_path, {"runtime": {"backend": "postgres", "url": "${MY_DB}"}})
    assert svc.target(StoreKind.RUNTIME).url == PG


def test_an_unset_variable_fails_with_the_setting_named(tmp_path: Path) -> None:
    """An unset var must not become a driver error about a host called '${MY_DB}'."""
    svc = _svc(tmp_path, {"runtime": {"backend": "postgres", "url": "${MY_DB}"}})
    with pytest.raises(StorageConfigError, match=re.escape("storage.runtime.url")):
        svc.target(StoreKind.RUNTIME)


# ---- refusing to degrade -------------------------------------------------------------------


def test_postgres_without_a_url_raises_rather_than_degrading(tmp_path: Path) -> None:
    """Falling back to SQLite writes the run to a different database than the one configured."""
    svc = _svc(tmp_path, {"runtime": {"backend": "postgres"}})
    with pytest.raises(StorageConfigError, match="has no URL"):
        svc.target(StoreKind.RUNTIME)


def test_an_unsupported_backend_raises(tmp_path: Path) -> None:
    svc = _svc(tmp_path, {"runtime": {"backend": "mysql", "url": "mysql://x/y"}})
    with pytest.raises(StorageConfigError, match="not supported"):
        svc.target(StoreKind.RUNTIME)


def test_an_enum_backend_is_compared_by_value(tmp_path: Path) -> None:
    """The schema models `backend` as an enum, and `Backend2.postgres == "postgres"` is False —
    which is how every workspace silently ran on SQLite before 1.127.0."""

    class _Backend:
        value = "postgres"

    svc = _svc(tmp_path, {"runtime": {"backend": _Backend(), "url": PG}})
    assert svc.target(StoreKind.RUNTIME).backend == "postgres"


# ---- checkpoints is deliberately different --------------------------------------------------


def test_checkpoints_does_not_inherit_runtime(tmp_path: Path) -> None:
    """The checkpointer is a LangGraph component with its own driver requirement. Promoting it to
    Postgres because the APPLICATION store is Postgres fails a workspace that never asked."""
    svc = _svc(tmp_path, {"runtime": {"backend": "postgres", "url": PG}})
    assert svc.target(StoreKind.CHECKPOINTS).backend == "sqlite"


def test_checkpoints_postgres_is_opt_in_and_says_what_is_missing(tmp_path: Path) -> None:
    svc = _svc(tmp_path, {"checkpoints": {"backend": "postgres", "url": PG}})
    try:
        import langgraph.checkpoint.postgres  # noqa: F401, PLC0415
    except ImportError:
        with pytest.raises(StorageConfigError, match=r"swarmkit-runtime\[postgres\]"):
            svc.target(StoreKind.CHECKPOINTS)
    else:
        assert svc.target(StoreKind.CHECKPOINTS).backend == "postgres"


def test_a_global_store_url_does_not_drag_checkpoints_along(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SWARMKIT_STORE_URL moves the application store. Setting it must not fail the run on a
    checkpointer driver the operator never asked for — found by the demo, which is the point."""
    monkeypatch.setenv("SWARMKIT_STORE_URL", PG)
    svc = _svc(tmp_path)
    assert svc.target(StoreKind.RUNTIME).backend == "postgres"
    assert svc.target(StoreKind.CHECKPOINTS).backend == "sqlite"


def test_an_explicit_checkpoints_block_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opt-in means opt-in: naming the block is how you ask for it."""
    monkeypatch.setenv("SWARMKIT_STORE_URL", PG)
    svc = _svc(tmp_path, {"checkpoints": {"backend": "sqlite"}})
    assert svc.target(StoreKind.CHECKPOINTS).backend == "sqlite"


# ---- one engine per database ----------------------------------------------------------------


def test_stores_on_the_same_url_share_one_engine(tmp_path: Path) -> None:
    """Six components opening six pools against one database is six times the connections."""
    svc = _svc(tmp_path)
    assert svc.engine(StoreKind.RUNTIME) is svc.engine(StoreKind.SAGA)


def test_audit_keeps_its_own_sqlite_file(tmp_path: Path) -> None:
    """Same backend, different file: the audit trail has its own retention and its own size."""
    svc = _svc(tmp_path)
    assert svc.target(StoreKind.AUDIT).url != svc.target(StoreKind.RUNTIME).url
    assert svc.target(StoreKind.AUDIT).url.endswith("audit.sqlite")


# ---- what it chose ---------------------------------------------------------------------------


def test_the_report_covers_every_kind_and_hides_the_password(tmp_path: Path) -> None:
    """The absence of this report is why a misconfigured workspace looked like an EMPTY one."""
    svc = _svc(tmp_path, {"runtime": {"backend": "postgres", "url": PG}})
    report = "\n".join(svc.report())
    for kind in StoreKind:
        assert kind.value in report
    assert "hunter2" not in report
    assert "***" in report


def test_a_populated_local_sqlite_under_remote_config_warns(tmp_path: Path) -> None:
    """Anyone upgrading into this change has rows in the old place. A silent cutover looks exactly
    like data loss."""
    local = _svc(tmp_path)
    local.store()  # creates .swarmkit/store.sqlite with the schema
    with local.engine(StoreKind.RUNTIME).begin() as conn:
        from sqlalchemy import text  # noqa: PLC0415

        conn.execute(
            text(
                "INSERT INTO jobs (id, topology, status, input, created_at) "
                "VALUES ('j1', 't', 'done', 'hello', '2026-08-02')"
            )
        )

    reset_storage_cache()
    remote = _svc(tmp_path, {"runtime": {"backend": "postgres", "url": PG}})
    warnings = "\n".join(remote.split_warnings())
    assert "store.sqlite" in warnings
    assert "migrate" in warnings


# ---- the rule that regressed three times ------------------------------------------------------

_SRC = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"


def test_nothing_outside_persistence_hardcodes_a_sqlite_path() -> None:
    """A hardcoded `sqlite:///…` IS the bug: it is a component deciding where data lives without
    consulting the config. Three of them shipped, in three different modules, over three releases.
    """
    offenders = []
    for path in _SRC.rglob("*.py"):
        rel = path.relative_to(_SRC).as_posix()
        if rel.startswith(("persistence/", "cli/_cmd_storage.py")):
            continue  # the service itself, and the migrator reading the OLD files by definition
        lines = path.read_text(encoding="utf-8").splitlines()
        for num, line in enumerate(lines, 1):
            if not re.search(r"[\"f]?[\"']sqlite:///", line):
                continue
            # The escape hatch, on the line or the one above it: constructors that materialise a
            # path the CALLER chose are fine — deciding the path without asking the config is not.
            context = line + (lines[num - 2] if num >= 2 else "")
            if "noqa: storage" in context:
                continue
            offenders.append(f"{rel}:{num}: {line.strip()}")
    assert not offenders, (
        "these decide where data lives instead of asking the storage service:\n"
        + "\n".join(offenders)
    )


def test_the_service_is_cached_per_workspace(tmp_path: Path) -> None:
    """Two calls must not produce two resolutions — that is how the split brain started."""
    assert storage_for_workspace(tmp_path) is storage_for_workspace(tmp_path)
