"""A fixture module with one stale feature flag, `checkout_v2_enabled`, at 100% rollout.

Deliberately minimal and never imported by the runtime — it exists so the cleanup harness has a real
before/after to produce a diff against.
"""

from flags import is_enabled


def checkout_total(items: list[float]) -> float:
    if is_enabled("checkout_v2_enabled"):
        # v2: tax applied per line (the winning branch — 100% rollout).
        return sum(round(i * 1.08, 2) for i in items)
    # v1: legacy flat tax (dead — nobody is on it).
    return round(sum(items) * 1.08, 2)
