"""Governance circuit breakers — prevent runaway agent execution.

Enforced at every node entry in the compiler (steps) and on every recorded model call (cost):
when a limit is exceeded the run ends with ``CircuitBreakerError`` naming the limit — not a silent
timeout. The tracker is installed per run by ``WorkspaceRuntime.run`` as a context variable, the
same scoping the run id and the stop checker use.

Defaults are sensible for development. Production workspaces should
configure explicit limits in workspace.yaml:

    governance:
      provider: agt
      limits:
        max_steps_per_agent: 20
        max_steps_per_run: 200

NOTE on cost tracking (max_cost_per_run_usd):
  Cost-based circuit breaking is NOT exposed yet. The plumbing exists
  (add_cost + check) but is disabled by default (None = unlimited).
  Accurate cost requires each ModelProvider to report per-call cost
  from the LLM provider's response — not static price tables.

  Strategy (to implement per-provider):
  - Anthropic: response.usage has input/output tokens; pricing via API
  - OpenAI: response.usage has tokens; cost varies by model
  - Google: response.usage_metadata has token counts
  - OpenRouter: response includes usage but cost varies by underlying model
  - Groq/Together: response.usage has tokens
  - Ollama: local, no cost

  Each provider's ModelProvider implementation must be updated to
  extract and return cost_usd from the completion response when the
  provider API supports it. Until then, max_cost_per_run_usd stays None.

See design/details/market-analysis-and-risk-mitigations.md (Risk 3).
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GovernanceLimits:
    """Circuit breaker thresholds for a topology run.

    All limits are optional — None means unlimited (no enforcement).
    When a limit is exceeded, the runtime raises CircuitBreakerError.
    """

    max_steps_per_agent: int | None = None
    max_steps_per_run: int | None = 500
    max_cost_per_run_usd: float | None = None


class CircuitBreakerError(Exception):
    """Raised when a governance limit is exceeded during execution."""

    def __init__(
        self, limit_name: str, limit_value: float | int, actual_value: float | int
    ) -> None:
        self.limit_name = limit_name
        self.limit_value = limit_value
        self.actual_value = actual_value
        super().__init__(
            f"Circuit breaker triggered: {limit_name} exceeded "
            f"(limit={limit_value}, actual={actual_value}). "
            f"Configure governance.limits.{limit_name} in workspace.yaml to adjust."
        )


class CircuitBreakerTracker:
    """Tracks execution metrics and raises when limits are exceeded.

    Created per-run. The compiler calls check_* methods at each step.
    """

    def __init__(self, limits: GovernanceLimits) -> None:
        self._limits = limits
        self._steps_per_agent: dict[str, int] = {}
        self._total_steps: int = 0
        self._total_cost_usd: float = 0.0

    @property
    def total_steps(self) -> int:
        return self._total_steps

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    def check_agent_step(self, agent_id: str) -> None:
        """Called before each agent execution step. Raises if limit exceeded."""
        self._steps_per_agent[agent_id] = self._steps_per_agent.get(agent_id, 0) + 1
        self._total_steps += 1

        if (
            self._limits.max_steps_per_agent is not None
            and self._steps_per_agent[agent_id] > self._limits.max_steps_per_agent
        ):
            raise CircuitBreakerError(
                "max_steps_per_agent",
                self._limits.max_steps_per_agent,
                self._steps_per_agent[agent_id],
            )

        if (
            self._limits.max_steps_per_run is not None
            and self._total_steps > self._limits.max_steps_per_run
        ):
            raise CircuitBreakerError(
                "max_steps_per_run",
                self._limits.max_steps_per_run,
                self._total_steps,
            )

    def add_cost(self, cost_usd: float) -> None:
        """Called after each LLM call with the incurred cost. Raises if limit exceeded."""
        self._total_cost_usd += cost_usd

        if (
            self._limits.max_cost_per_run_usd is not None
            and self._total_cost_usd > self._limits.max_cost_per_run_usd
        ):
            raise CircuitBreakerError(
                "max_cost_per_run_usd",
                self._limits.max_cost_per_run_usd,
                self._total_cost_usd,
            )

    def get_agent_steps(self, agent_id: str) -> int:
        """Return current step count for an agent."""
        return self._steps_per_agent.get(agent_id, 0)


#: The tracker for the run on this task — installed by ``WorkspaceRuntime.run`` for the run's
#: duration, read by the compiler at every node entry, the same scoping the run id and the stop
#: checker use. ``None`` outside a run (a bare compile, a unit test) means no enforcement, which is
#: what the limits' own ``None`` means.
_tracker: ContextVar[CircuitBreakerTracker | None] = ContextVar(
    "swarmkit_circuit_breaker", default=None
)


def current_tracker() -> CircuitBreakerTracker | None:
    return _tracker.get()


def set_run_tracker(tracker: CircuitBreakerTracker | None) -> Token[CircuitBreakerTracker | None]:
    return _tracker.set(tracker)


def reset_run_tracker(token: Token[CircuitBreakerTracker | None]) -> None:
    _tracker.reset(token)


def limits_from_workspace(raw_workspace: Any) -> GovernanceLimits:
    """``governance.limits`` from the workspace model, or the defaults when absent.

    Until 1.227.0 this block was accepted by the schema, documented as a circuit breaker, and read
    by nothing — `max_steps_per_agent: 20` in a workspace.yaml had no effect on any run.
    """
    gov = getattr(raw_workspace, "governance", None)
    limits = getattr(gov, "limits", None) if gov is not None else None
    if limits is None:
        return GovernanceLimits()
    get = limits.get if isinstance(limits, dict) else lambda k, d=None: getattr(limits, k, d)
    per_run = get("max_steps_per_run")
    return GovernanceLimits(
        max_steps_per_agent=get("max_steps_per_agent"),
        max_steps_per_run=per_run if per_run is not None else GovernanceLimits().max_steps_per_run,
        max_cost_per_run_usd=get("max_cost_per_run_usd"),
    )
