"""The integration-contract lock manager.

The controller *is* the lock manager (design "Integration-contract locking"). Locks are
per-contract (per app-pair), not global, so disjoint requirements run in parallel; a requirement
needing several contracts acquires them **all-or-none in a fixed global order** (deadlock
avoidance) and, if any is held, queues without taking the others. A contended requirement parks;
release resumes the queue in FIFO order. See design/details/pipeline-controller.md.
"""

from __future__ import annotations


class LockManager:
    """Per-contract mutual exclusion with all-or-none acquisition + a FIFO wait queue."""

    def __init__(self) -> None:
        self._holder: dict[str, str] = {}  # lock id -> holding requirement id
        self._queue: dict[str, list[str]] = {}  # lock id -> FIFO of waiting requirement ids

    @staticmethod
    def _order(lock_ids: set[str] | tuple[str, ...] | list[str]) -> list[str]:
        # Fixed global order (lexicographic) — the deadlock-avoidance mechanism.
        return sorted(set(lock_ids))

    def try_acquire(self, requirement_id: str, lock_ids: tuple[str, ...]) -> bool:
        """Acquire every lock all-or-none. On failure, take none and queue for the held ones."""
        ordered = self._order(lock_ids)
        blocked = [
            lid for lid in ordered if lid in self._holder and self._holder[lid] != requirement_id
        ]
        if blocked:
            # All-or-none: take nothing; queue behind the contended locks (dedup the entry).
            for lid in blocked:
                waiters = self._queue.setdefault(lid, [])
                if requirement_id not in waiters:
                    waiters.append(requirement_id)
            return False
        for lid in ordered:
            self._holder[lid] = requirement_id
        return True

    def release(self, requirement_id: str, lock_ids: set[str] | tuple[str, ...]) -> list[str]:
        """Release the given locks held by this requirement.

        Returns the ordered list of distinct waiting requirement ids that were unblocked (a
        contended lock freed) so the controller can resume their parked stages. FIFO per lock.
        """
        resumed: list[str] = []
        for lid in self._order(lock_ids):
            if self._holder.get(lid) == requirement_id:
                del self._holder[lid]
            waiters = self._queue.get(lid)
            if waiters:
                nxt = waiters.pop(0)
                if not waiters:
                    del self._queue[lid]
                if nxt not in resumed:
                    resumed.append(nxt)
        return resumed

    def holder(self, lock_id: str) -> str | None:
        return self._holder.get(lock_id)

    def held_by(self, requirement_id: str) -> list[str]:
        return sorted(lid for lid, holder in self._holder.items() if holder == requirement_id)


__all__ = ["LockManager"]
