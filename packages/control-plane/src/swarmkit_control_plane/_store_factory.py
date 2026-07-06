"""Registry factory — selects the store backend for ``serve`` from env config.

Resolution order (mirrors the runtime store factory, kept standalone per design D1):
1. ``SWARMKIT_CONTROL_PLANE_STORE_BACKEND`` env var (``sqlite`` or ``postgres``)
2. Default: ``sqlite`` at ``{data_dir}/registry.sqlite``

For postgres the URL comes from ``SWARMKIT_CONTROL_PLANE_STORE_URL`` or ``DATABASE_URL``. The four
panel stores share one database, so only the registry needs building here — ``create_app`` derives
the others from ``registry.engine``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from swarmkit_control_plane._engine import make_engine, sqlite_url
from swarmkit_control_plane._registry import SqliteRegistry

logger = logging.getLogger("swarmkit.control_plane.store")


def resolve_backend(data_dir: Path) -> tuple[str, str]:
    """Resolve ``(backend, url)`` from env, applying the fallback rule. Pure (no connection).

    ``backend=postgres`` with no URL degrades to sqlite (with a warning) — a misconfiguration
    shouldn't take the panel down.
    """
    backend = os.environ.get("SWARMKIT_CONTROL_PLANE_STORE_BACKEND", "").lower() or "sqlite"
    url = os.environ.get("SWARMKIT_CONTROL_PLANE_STORE_URL") or os.environ.get("DATABASE_URL") or ""
    if backend == "postgres" and not url:
        logger.warning(
            "SWARMKIT_CONTROL_PLANE_STORE_BACKEND=postgres but no URL configured. "
            "Set DATABASE_URL or SWARMKIT_CONTROL_PLANE_STORE_URL. Falling back to sqlite."
        )
        backend = "sqlite"
    if backend == "sqlite":
        url = sqlite_url(data_dir / "registry.sqlite")
    return backend, url


def create_registry(data_dir: Path) -> SqliteRegistry:
    """Build the registry (and thereby the shared engine) for the configured backend."""
    backend, url = resolve_backend(data_dir)
    if backend == "postgres":
        logger.info("Control-plane store backend: postgres (%s...)", url[:30])
        return SqliteRegistry(make_engine(url))
    logger.info("Control-plane store backend: sqlite (%s)", data_dir)
    return SqliteRegistry(data_dir / "registry.sqlite")


__all__ = ["create_registry", "resolve_backend"]
