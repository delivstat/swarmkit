"""AuthProviderRegistry — maps provider names to factory functions.

Pre-registers the built-in providers (``none``, ``api_key``). Plugin
providers can register additional factories at import time or via
entry points in future milestones.
"""

from __future__ import annotations

from collections.abc import Callable

from swarmkit_runtime.auth._provider import AuthProvider


class AuthProviderRegistry:
    """Simple dict-based registry: name -> factory function."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., AuthProvider]] = {}

    def register(self, name: str, factory: Callable[..., AuthProvider]) -> None:
        """Register a provider factory under *name*."""
        self._factories[name] = factory

    def get(self, name: str) -> Callable[..., AuthProvider] | None:
        """Return the factory for *name*, or None if not registered."""
        return self._factories.get(name)

    @property
    def names(self) -> list[str]:
        """List registered provider names."""
        return sorted(self._factories.keys())


def _default_registry() -> AuthProviderRegistry:
    """Build the registry with built-in providers pre-registered."""
    from swarmkit_runtime.auth._api_key import APIKeyAuthProvider  # noqa: PLC0415
    from swarmkit_runtime.auth._jwt import JWTAuthProvider  # noqa: PLC0415
    from swarmkit_runtime.auth._none import NoneAuthProvider  # noqa: PLC0415

    registry = AuthProviderRegistry()
    registry.register("none", lambda **_kw: NoneAuthProvider())
    registry.register("api_key", lambda **kw: APIKeyAuthProvider(keys=kw.get("keys", [])))
    registry.register(
        "jwt",
        lambda **kw: JWTAuthProvider(
            issuer=kw["issuer"],
            audience=kw.get("audience", "swarmkit"),
            jwks_url=kw.get("jwks_url"),
            scopes_claim=kw.get("scopes_claim", "scope"),
        ),
    )

    return registry


#: Module-level singleton — importable for convenience.
default_registry: AuthProviderRegistry = _default_registry()
