"""ModelProvider registry — maps provider_id to provider instances.

Resolution order when a topology references ``provider: foo``:
workspace overrides → entry-point plugins → built-ins. First match wins.
Duplicate IDs fail topology load.

See ``design/details/model-provider-abstraction.md``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelProviderProtocol(Protocol):
    """Structural type for any model provider (ABC not enforced)."""

    provider_id: str

    async def complete(self, request: Any) -> Any: ...
    def supports(self, model: str) -> bool: ...


class ProviderRegistry:
    """Registry of model providers keyed by ``provider_id``."""

    def __init__(self) -> None:
        self._providers: dict[str, ModelProviderProtocol] = {}

    def register(self, provider: ModelProviderProtocol) -> None:
        pid = provider.provider_id
        if pid in self._providers:
            raise ValueError(
                f"Duplicate model provider id '{pid}'. "
                f"Already registered: {type(self._providers[pid]).__name__}; "
                f"conflicting: {type(provider).__name__}."
            )
        self._providers[pid] = provider

    def get(self, provider_id: str) -> ModelProviderProtocol | None:
        return self._providers.get(provider_id)

    def resolve(self, provider_id: str, model: str) -> ModelProviderProtocol:
        provider = self._providers.get(provider_id)
        if provider is None:
            available = sorted(self._providers.keys()) or ["(none)"]
            raise LookupError(
                f"Model provider '{provider_id}' is not registered. "
                f"Available: {', '.join(available)}. "
                f"Check workspace.yaml model_providers or install the provider package."
            )
        if not provider.supports(model):
            raise LookupError(f"Provider '{provider_id}' does not support model '{model}'.")
        return provider

    @property
    def provider_ids(self) -> list[str]:
        return sorted(self._providers.keys())

    def __len__(self) -> int:
        return len(self._providers)
