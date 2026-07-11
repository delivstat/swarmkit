"""Executor registry (design/details/executor-abstraction.md §4.2, §5).

`executor.kind` is not a closed enum in core — it is validated against this registry at runtime, so
a new kind (a harness adapter) is added by registering an :class:`Executor`, no core schema change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from swarmkit_runtime.executors._adapter_spec import AdapterSpec
from swarmkit_runtime.executors._declarative import DeclarativeExecutor, load_adapter_specs
from swarmkit_runtime.executors._model import ModelExecutor
from swarmkit_runtime.executors._protocol import Executor, ExecutorError, ResolvedExecutor


class ExecutorRegistry:
    """Maps an executor ``kind`` to its :class:`Executor`, and resolves an archetype's block."""

    def __init__(self) -> None:
        self._kinds: dict[str, Executor] = {}

    def register(self, executor: Executor) -> None:
        self._kinds[executor.kind] = executor

    def kinds(self) -> list[str]:
        return sorted(self._kinds)

    def resolve(self, block: Any | None) -> ResolvedExecutor:
        """Resolve an archetype's ``executor`` block (a pydantic ``Executor`` or ``None``) to a
        :class:`ResolvedExecutor`. ``None`` → the default ``model`` executor (backward compat).
        Raises :class:`ExecutorError` on an unknown kind or invalid config."""
        kind = getattr(block, "kind", None) or "model"
        executor = self._kinds.get(kind)
        if executor is None:
            raise ExecutorError(f"unknown executor kind {kind!r}; registered: {self.kinds()}")
        config: Mapping[str, Any] = getattr(block, "config", None) or {}
        executor.validate_config(config)
        return ResolvedExecutor(kind=kind, ref=getattr(block, "ref", None), config=dict(config))


def default_executor_registry(
    adapter_specs: Iterable[AdapterSpec] | None = None,
) -> ExecutorRegistry:
    """The core registry used to validate ``executor.kind`` at resolution.

    Registers ``model`` (the default) plus one entry per declarative adapter: the bundled reference
    library (claude-code, codex, …) always, and any ``adapter_specs`` supplied by the caller (a
    workspace's own ``adapters/``). Each declarative kind is registered as a
    :class:`DeclarativeExecutor` — used here only for kind + config validation; a running node
    builds its own configured instance. No harness is special-cased in code."""
    registry = ExecutorRegistry()
    registry.register(ModelExecutor())
    specs: dict[str, AdapterSpec] = dict(load_adapter_specs(None))  # bundled
    for spec in adapter_specs or ():
        specs[spec.kind] = spec  # workspace overrides bundled
    for spec in specs.values():
        registry.register(DeclarativeExecutor(spec))
    return registry
