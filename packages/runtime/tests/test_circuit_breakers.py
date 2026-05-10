"""Tests for governance circuit breakers (M6 PR 5)."""

from __future__ import annotations

import pytest
from swarmkit_runtime.governance import (
    CircuitBreakerError,
    CircuitBreakerTracker,
    GovernanceLimits,
)


class TestGovernanceLimits:
    def test_defaults(self) -> None:
        limits = GovernanceLimits()
        assert limits.max_steps_per_agent is None
        assert limits.max_steps_per_run == 500
        assert limits.max_cost_per_run_usd is None

    def test_custom_limits(self) -> None:
        limits = GovernanceLimits(
            max_steps_per_agent=10,
            max_steps_per_run=100,
            max_cost_per_run_usd=5.0,
        )
        assert limits.max_steps_per_agent == 10
        assert limits.max_steps_per_run == 100
        assert limits.max_cost_per_run_usd == 5.0

    def test_all_unlimited(self) -> None:
        limits = GovernanceLimits(
            max_steps_per_agent=None,
            max_steps_per_run=None,
            max_cost_per_run_usd=None,
        )
        assert limits.max_steps_per_run is None


class TestCircuitBreakerTracker:
    def test_tracks_agent_steps(self) -> None:
        tracker = CircuitBreakerTracker(GovernanceLimits(max_steps_per_agent=None))
        tracker.check_agent_step("agent-1")
        tracker.check_agent_step("agent-1")
        tracker.check_agent_step("agent-2")

        assert tracker.get_agent_steps("agent-1") == 2
        assert tracker.get_agent_steps("agent-2") == 1
        assert tracker.total_steps == 3

    def test_max_steps_per_agent_triggers(self) -> None:
        tracker = CircuitBreakerTracker(
            GovernanceLimits(max_steps_per_agent=3, max_steps_per_run=None)
        )

        tracker.check_agent_step("a")
        tracker.check_agent_step("a")
        tracker.check_agent_step("a")

        with pytest.raises(CircuitBreakerError) as exc_info:
            tracker.check_agent_step("a")

        assert exc_info.value.limit_name == "max_steps_per_agent"
        assert exc_info.value.limit_value == 3
        assert exc_info.value.actual_value == 4
        assert "governance.limits.max_steps_per_agent" in str(exc_info.value)

    def test_max_steps_per_run_triggers(self) -> None:
        tracker = CircuitBreakerTracker(GovernanceLimits(max_steps_per_run=5))

        for i in range(5):
            tracker.check_agent_step(f"agent-{i}")

        with pytest.raises(CircuitBreakerError) as exc_info:
            tracker.check_agent_step("agent-x")

        assert exc_info.value.limit_name == "max_steps_per_run"
        assert exc_info.value.limit_value == 5
        assert exc_info.value.actual_value == 6

    def test_max_cost_triggers(self) -> None:
        tracker = CircuitBreakerTracker(
            GovernanceLimits(max_cost_per_run_usd=1.0, max_steps_per_run=None)
        )

        tracker.add_cost(0.5)
        tracker.add_cost(0.4)
        assert tracker.total_cost_usd == pytest.approx(0.9)

        with pytest.raises(CircuitBreakerError) as exc_info:
            tracker.add_cost(0.2)

        assert exc_info.value.limit_name == "max_cost_per_run_usd"
        assert exc_info.value.limit_value == 1.0

    def test_no_limits_no_error(self) -> None:
        tracker = CircuitBreakerTracker(
            GovernanceLimits(
                max_steps_per_agent=None, max_steps_per_run=None, max_cost_per_run_usd=None
            )
        )

        for _ in range(1000):
            tracker.check_agent_step("a")
        tracker.add_cost(999.99)

        assert tracker.total_steps == 1000
        assert tracker.total_cost_usd == pytest.approx(999.99)

    def test_different_agents_tracked_separately(self) -> None:
        tracker = CircuitBreakerTracker(
            GovernanceLimits(max_steps_per_agent=3, max_steps_per_run=None)
        )

        for _ in range(3):
            tracker.check_agent_step("a")
        for _ in range(3):
            tracker.check_agent_step("b")

        with pytest.raises(CircuitBreakerError):
            tracker.check_agent_step("a")

        # agent-b still has room... wait no, it's also at 3
        with pytest.raises(CircuitBreakerError):
            tracker.check_agent_step("b")


class TestCircuitBreakerError:
    def test_error_message(self) -> None:
        err = CircuitBreakerError("max_steps_per_run", 500, 501)
        assert "Circuit breaker triggered" in str(err)
        assert "max_steps_per_run" in str(err)
        assert "limit=500" in str(err)
        assert "actual=501" in str(err)
        assert "workspace.yaml" in str(err)
