"""DeploySeqStore — the panel's monotonic per-deploy counter (design 22 downgrade guard).

Every deploy the panel signs stamps a strictly-increasing sequence into the signature. The instance
records the high-water mark per (fleet, artifact) and rejects any deploy whose sequence isn't newer,
so an *old* validly-signed deploy can't be replayed over a newer version. A single counter per panel
(monotonic over time for any artifact) is sufficient; the instance does the per-artifact tracking.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from swarmkit_control_plane._store_base import Store
from swarmkit_control_plane._tables import fleet_deploy_seq

_SINGLETON = "default"


class DeploySeqStore(Store):
    """A persisted, atomically-incremented deploy sequence."""

    def __init__(self, backing: Any) -> None:
        super().__init__(backing)

    def next(self) -> int:
        """Return the next sequence — strictly greater than every prior call. Increment-under-lock,
        so concurrent deploys get distinct, increasing numbers."""
        with self._lock, self._engine.begin() as conn:
            row = (
                conn.execute(
                    select(fleet_deploy_seq.c.value).where(fleet_deploy_seq.c.id == _SINGLETON)
                )
                .mappings()
                .first()
            )
            if row is None:
                conn.execute(fleet_deploy_seq.insert().values(id=_SINGLETON, value=1))
                return 1
            nxt = int(row["value"]) + 1
            conn.execute(
                update(fleet_deploy_seq)
                .where(fleet_deploy_seq.c.id == _SINGLETON)
                .values(value=nxt)
            )
        return nxt


__all__ = ["DeploySeqStore"]
