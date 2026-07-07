"""InstanceStateStore — the panel's cache of each instance's last-pulled full state.

The panel pulls ``GET /fleet/state`` from an instance (Mode A) and stores the returned
``InstanceState`` here, so the instance's full inventory (topologies/skills/archetypes with content)
stays inspectable even when the instance is **offline** — the source of truth is the instance's
YAML; this is the observed-state cache (fleet enrollment Phase 1, design 19). Kept **separate** from
the deployable artifact registry (design 15); "adopt into registry" is an explicit action.

SQLAlchemy Core over SQLite (default) or Postgres — same store model as the other four stores.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from swarmkit_control_plane._store_base import Store, upsert
from swarmkit_control_plane._tables import instance_state


class InstanceStateStore(Store):
    """Thread-safe cache of the last observed ``InstanceState`` per instance."""

    def put(self, instance_id: str, state: dict[str, Any]) -> str:
        """Cache (replace) an instance's full state. Returns the ``synced_at`` timestamp."""
        synced_at = datetime.now(UTC).isoformat()
        with self._lock, self._engine.begin() as conn:
            conn.execute(
                upsert(
                    self._engine,
                    instance_state,
                    {"instance_id": instance_id, "state": state, "synced_at": synced_at},
                    index_elements=["instance_id"],
                    set_={"state": state, "synced_at": synced_at},
                )
            )
        return synced_at

    def get(self, instance_id: str) -> dict[str, Any] | None:
        """Return ``{state, synced_at}`` for an instance, or ``None`` if never synced."""
        with self._lock, self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(instance_state.c.state, instance_state.c.synced_at).where(
                        instance_state.c.instance_id == instance_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return {"state": row["state"], "synced_at": row["synced_at"]}
