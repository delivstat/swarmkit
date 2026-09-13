"""ModelProvider registry — maps provider_id to provider instances.

Resolution order when a topology references ``provider: foo``:
workspace overrides → entry-point plugins → built-ins. First match wins.
Duplicate IDs fail topology load.

See ``design/details/model-provider-abstraction.md``.
"""

from __future__ import annotations

from pathlib import Path
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


def provider_enforces_response_schema(
    provider_id: str, workspace_root: Path | str | None = None
) -> bool:
    """Whether ``provider_id`` constrains decoding to the response schema.

    Deliberately NOT part of ``ModelProviderProtocol``: it is an optional capability, and requiring
    every provider to declare it would break third-party ones — and every existing test double —
    for a field most of them have no opinion about. Providers opt in with a class attribute; silence
    means no.

    Read from the family classes and the provider YAMLs rather than a second table, so the answer
    cannot drift from the declaration. Resolved by id because the compiler builds the system
    prompt before any provider instance is in hand, and it needs to know whether to paste the
    schema into the prompt.

    Unknown or unimportable providers answer **False** — the conservative direction. A provider
    that does not constrain decoding needs the schema in its prompt, so guessing "enforced" would
    silently remove the only thing carrying the shape.
    """
    from ._declarative import (  # noqa: PLC0415
        FAMILIES,
        ProviderSpecError,
        enforces_response_schema,
        load_provider_specs,
        resolve_chain,
    )

    if provider_id == "mock":
        return False
    # Declared providers — bundled, or the workspace's when a root is given — answer through
    # their family, narrowed by the YAML's ``capabilities.structured_output``.
    try:
        specs = load_provider_specs(workspace_root)
        if provider_id in specs:
            return enforces_response_schema(resolve_chain(provider_id, specs))
    except ProviderSpecError:
        return False
    if provider_id in FAMILIES:
        try:
            from ._declarative import family_class  # noqa: PLC0415

            return bool(family_class(provider_id).enforces_response_schema)
        except Exception:  # pragma: no cover - the family's SDK is an optional extra
            return False
    return False
