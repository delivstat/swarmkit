"""Store factory — selects backend based on workspace config or env var.

Resolution order:
1. ``SWARMKIT_STORE_BACKEND`` env var (``sqlite`` or ``postgres``)
2. ``workspace.yaml`` ``storage.runtime.backend`` field
3. Default: ``sqlite``

For postgres, the connection URL is resolved from:
1. ``SWARMKIT_STORE_URL`` or ``DATABASE_URL`` env var
2. ``workspace.yaml`` ``storage.runtime.url`` field

``workspace.raw`` is a parsed-YAML ``Mapping``, so config is read through :func:`_field`, which
handles both a Mapping and a typed model. A backend that cannot be honoured raises
:class:`StoreConfigError` — it does not fall back.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from swarmkit_runtime.persistence._store import SqliteStore, Store, make_engine

logger = logging.getLogger("swarmkit.persistence")


class StoreConfigError(RuntimeError):
    """The storage config names a backend that cannot be honoured.

    Raised rather than degraded: a silent fallback writes the run to a different database than the
    one configured, and splits serve from the orchestrator with neither process warning.
    """


def _field(obj: Any, key: str) -> Any:
    """Read *key* off a parsed-YAML ``Mapping`` **or** a typed model.

    Both shapes reach here. ``ResolvedWorkspace.raw`` is a typed ``SwarmKitWorkspace``; the fleet
    factory hands over a plain dict parsed straight from workspace.yaml. Plain ``getattr`` returns
    the default for a Mapping, which made ``storage.runtime`` dead config for a dict caller.
    """
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _text(value: Any) -> str:
    """Normalise a config scalar to a plain lowercase string.

    The schema models ``backend`` as an **enum**, and ``Backend2.postgres`` is not a ``str``:
    ``Backend2.postgres == "postgres"`` is False and ``str()`` yields ``"Backend2.postgres"``. So
    on the serve path — which passes the typed model — the backend comparison was always False and
    every workspace silently ran on sqlite however it was configured. Unwrap ``.value`` first.
    """
    if value is None:
        return ""
    inner = getattr(value, "value", value)
    return str(inner).strip().lower()


def _resolve_backend(workspace_path: Path, workspace_raw: Any = None) -> tuple[str, str, str]:
    """Resolve ``(backend, url, source)`` from env + workspace config.

    Pure (no DB connection) so backend selection is unit-testable. Precedence: env var, then
    ``storage.runtime``, then the sqlite default. ``source`` names where the answer came from, so a
    surprising backend is visible in the log rather than inferred.

    A backend naming a real database with no resolvable URL **raises**. It used to warn and degrade
    to sqlite; for a storage backend that is the wrong direction — a run that silently writes to a
    different database than the one configured is worse than a startup failure, and it also splits
    serve from the orchestrator without either process noticing.
    """
    backend = os.environ.get("SWARMKIT_STORE_BACKEND", "").lower()
    url = os.environ.get("SWARMKIT_STORE_URL") or os.environ.get("DATABASE_URL", "")
    source = "env" if backend else ""

    if not backend and workspace_raw is not None:
        runtime_cfg = _field(_field(workspace_raw, "storage"), "runtime")
        if runtime_cfg is not None:
            backend = _text(_field(runtime_cfg, "backend"))
            if backend:
                source = "workspace.yaml"
            if not url:
                raw_url = _field(runtime_cfg, "url")
                url = str(getattr(raw_url, "value", raw_url) or "")

    if not backend:
        backend, source = "sqlite", source or "default"
    if backend == "postgres" and not url:
        raise StoreConfigError(
            f"storage backend 'postgres' (from {source}) has no URL. Set storage.runtime.url, "
            "SWARMKIT_STORE_URL or DATABASE_URL. Refusing to fall back to sqlite: a run would "
            "write to a different database than the one configured."
        )
    return backend, url, source


def create_store(
    workspace_path: Path,
    workspace_raw: Any = None,
) -> Store:
    """Create the persistence store for the configured backend.

    SQLite (the default) lives at ``{workspace}/.swarmkit/store.sqlite``. Postgres is used when
    ``storage.runtime.backend=postgres`` (or ``SWARMKIT_STORE_BACKEND=postgres``) *and* a URL is
    configured — the same SQLAlchemy-Core ``Store``, just a different dialect
    (design/details/postgres-backend.md).
    """
    backend, url, source = _resolve_backend(workspace_path, workspace_raw)
    if backend == "postgres":
        logger.info("Store backend: postgres (source: %s, %s...)", source, url[:30])
        return Store(make_engine(url))
    logger.info("Store backend: sqlite (source: %s, path: %s)", source, workspace_path)
    return SqliteStore(workspace_path)


__all__ = ["StoreConfigError", "create_store"]
