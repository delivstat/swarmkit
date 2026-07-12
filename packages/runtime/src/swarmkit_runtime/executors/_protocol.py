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
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from swarmkit_runtime.executors._events import ExecEvent
from swarmkit_runtime.executors._run import (
    BudgetEnvelope,
    PreflightReport,
    ResumeToken,
    SandboxHandle,
    TaskSpec,
)


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

    # --- execution hooks (§5) --------------------------------------------------------------------
    # Default to "not implemented" so a `model` executor (which the compiler runs via its existing
    # node, not through run()) stays minimal; the harness executor overrides these in P2.

    def preflight(self, task: TaskSpec, sandbox: SandboxHandle) -> PreflightReport:
        """Fail-fast readiness check before any spend (binary/version/credentials/sandbox)."""
        raise NotImplementedError(f"executor {self.kind!r} does not implement preflight()")

    def run(
        self,
        task: TaskSpec,
        sandbox: SandboxHandle,
        budget: BudgetEnvelope,
        *,
        resume_token: str | None = None,
        granted: tuple[str, ...] = (),
    ) -> AsyncIterator[ExecEvent]:
        """Launch, translating the vendor's native stream into normalized :data:`ExecEvent`s. Core
        supplies budget/sandbox enforcement around this; the adapter enforces nothing itself.

        ``resume_token`` / ``granted`` drive a park-resume relaunch (RFC §6.2): resume the prior
        session with an expanded grant. Executors that don't support relay ignore them."""
        raise NotImplementedError(f"executor {self.kind!r} does not implement run()")

    async def cancel(self, run_id: str) -> None:
        """Cancel an in-flight run. No-op by default."""
        return None

    def resume_token(self, run_id: str) -> ResumeToken | None:
        """The vendor session id if the kind supports resume, else ``None`` (default)."""
        return None
