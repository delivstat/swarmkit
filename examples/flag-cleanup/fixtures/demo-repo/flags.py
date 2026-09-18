"""A tiny dependency-injected flag wrapper — the pattern the cleanup must account for."""

_ROLLOUT = {"checkout_v2_enabled": 100}


def is_enabled(key: str) -> bool:
    return _ROLLOUT.get(key, 0) >= 100
