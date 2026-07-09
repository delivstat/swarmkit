"""MembershipStore factory — honour the instance's configured storage backend (design 19 Q4).

The fleet enrollment store (memberships, pinned fleet keys, enrollment tokens, deploy sequences)
must live in the **same backend** as the rest of the instance's state: on a Postgres deployment it
belongs in Postgres, not stranded in a local sqlite file. This resolves the store location the same
way ``create_store`` does (env → ``storage.runtime`` config → sqlite default), so serve, the CLI,
and the Mode-B connector all agree on one location.

- **sqlite (default):** a *dedicated* ``{workspace}/.swarmkit/fleet.sqlite`` — kept separate from
  the main ``store.sqlite`` (the enrollment credentials are security-sensitive; separation is
  deliberate). Existing files keep working — back-compatible.
- **postgres:** the configured Postgres DB (the ``fleet_*`` tables alongside the main store's), so
  a Postgres instance keeps *all* its state in one place.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from swarmkit_runtime.persistence._factory import _resolve_backend
from swarmkit_runtime.persistence._store import make_engine


def _storage_config_from_yaml(workspace_path: Path) -> Any | None:
    """Read only ``storage.runtime.{backend,url}`` from workspace.yaml as a shim that
    ``_resolve_backend`` can consume — so a caller without the fully-resolved workspace model (the
    CLI) still honours a workspace-configured backend, without loading the whole workspace."""
    wy = workspace_path / "workspace.yaml"
    if not wy.exists():
        return None
    try:
        data = yaml.safe_load(wy.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    runtime_cfg = (data.get("storage") or {}).get("runtime") or {}
    if not runtime_cfg:
        return None
    return SimpleNamespace(
        storage=SimpleNamespace(
            runtime=SimpleNamespace(
                backend=runtime_cfg.get("backend", ""), url=runtime_cfg.get("url", "")
            )
        )
    )


def create_membership_store(workspace_path: Path, workspace_raw: Any = None) -> Any:
    """Build the instance's :class:`MembershipStore` on the configured backend (design 19 Q4).

    ``workspace_raw`` is the resolved workspace model when the caller has it (serve); when omitted
    (the CLI) the storage block is read straight from workspace.yaml so the backend still matches.
    """
    from swarmkit_runtime.fleet._store import MembershipStore  # noqa: PLC0415

    if workspace_raw is None:
        workspace_raw = _storage_config_from_yaml(workspace_path)
    backend, url = _resolve_backend(workspace_path, workspace_raw)
    if backend == "postgres":
        return MembershipStore(make_engine(url))
    return MembershipStore(workspace_path)


__all__ = ["create_membership_store"]
