"""Executor abstraction (design/details/executor-abstraction.md).

The node-execution *provider seam* — HOW a node does its work, decoupled from WHAT it does. `model`
is one kind (today's behavior); `harness` (an external agentic harness) lands as a kind in P2. Same
class of narrow, swapped-at-startup interface as ``ModelProvider`` / ``GovernanceProvider`` — not a
capability primitive (see CLAUDE.md invariant #2).

P1 defines the resolution + validation surface (``config_schema``); the execution hooks
(``preflight`` / ``run`` / ``cancel`` / ``resume_token``, §5) land with the harness executor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar


class ExecutorError(Exception):
    """An ``executor`` block that can't be resolved or validated (unknown kind, bad config)."""


@dataclass(frozen=True)
class ResolvedExecutor:
    """The executor a node runs, resolved from an archetype's ``executor`` block (or the model
    default). ``config`` is opaque to core — the executor kind owns its meaning."""

    kind: str
    ref: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)


class Executor(ABC):
    """A registered executor kind. Owns validation of its opaque ``config`` block (§4.2)."""

    kind: ClassVar[str]

    @abstractmethod
    def config_schema(self) -> dict[str, Any]:
        """JSON Schema for this kind's ``config`` block."""

    def validate_config(self, config: Mapping[str, Any]) -> None:
        """Raise :class:`ExecutorError` if ``config`` does not satisfy :meth:`config_schema`."""
        import jsonschema  # noqa: PLC0415

        try:
            jsonschema.validate(dict(config), self.config_schema())
        except jsonschema.ValidationError as exc:
            raise ExecutorError(f"executor {self.kind!r} config invalid: {exc.message}") from exc
