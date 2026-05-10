"""Governance circuit breakers — prevent runaway agent execution.

Enforced inside the compiler's agent execution loop. When a limit is
exceeded, execution aborts with a clear error — not a silent timeout.

Defaults are sensible for development. Production workspaces should
configure explicit limits in workspace.yaml:

    governance:
      provider: agt
      limits:
        max_steps_per_agent: 20
        max_steps_per_run: 200
        max_cost_per_run_usd: 5.00

See design/details/market-analysis-and-risk-mitigations.md (Risk 3).
"""

from __future__ import annotations

from dataclasses import dataclass


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
