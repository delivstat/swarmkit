"""Store factory — selects backend based on workspace config or env var.

Resolution order:
1. ``SWARMKIT_STORE_BACKEND`` env var (``sqlite`` or ``postgres``)
2. ``workspace.yaml`` ``storage.runtime.backend`` field
3. Default: ``sqlite``

For postgres, the connection URL is resolved from:
1. ``SWARMKIT_STORE_URL`` or ``DATABASE_URL`` env var
2. ``workspace.yaml`` ``storage.runtime.url`` field
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from swarmkit_runtime.persistence._sqlite import SqliteStore

logger = logging.getLogger("swarmkit.persistence")


def create_store(
    workspace_path: Path,
    workspace_raw: Any = None,
) -> SqliteStore:
    """Create the appropriate store backend.

    Currently only SQLite is implemented. When Postgres is needed,
    this factory will return a ``PostgresStore`` with the same API.
    """
    backend = os.environ.get("SWARMKIT_STORE_BACKEND", "").lower()
    url = os.environ.get("SWARMKIT_STORE_URL") or os.environ.get("DATABASE_URL", "")

    if not backend and workspace_raw is not None:
        storage = getattr(workspace_raw, "storage", None)
        if storage is not None:
            runtime_cfg = getattr(storage, "runtime", None)
            if runtime_cfg is not None:
                backend = getattr(runtime_cfg, "backend", "") or ""
                if not url:
                    url = getattr(runtime_cfg, "url", "") or ""

    backend = backend or "sqlite"

    if backend == "postgres":
        if not url:
            logger.warning(
                "storage.runtime.backend=postgres but no URL configured. "
                "Set DATABASE_URL or SWARMKIT_STORE_URL or storage.runtime.url. "
                "Falling back to sqlite."
            )
            backend = "sqlite"
        else:
            logger.info(
                "Postgres store requested but not yet implemented. Falling back to sqlite. URL: %s",
                url[:30] + "...",
            )
            backend = "sqlite"

    logger.info("Store backend: %s (path: %s)", backend, workspace_path)
    return SqliteStore(workspace_path)


__all__ = ["create_store"]
