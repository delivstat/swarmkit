"""A2A budget forwarding (a2a-federation.md, slice 2): the caller's remaining allowance rides the
message metadata and a SwarmKit callee installs it as the child run's circuit-breaker limits.

Unit level here (the helpers); the end-to-end enforce-on-callee path is in test_a2a_serve.
"""

from __future__ import annotations

from swarmkit_runtime.governance._limits import (
    CircuitBreakerTracker,
    GovernanceLimits,
    apply_budget_override,
)


def test_apply_budget_override_takes_the_stricter_per_dimension() -> None:
    base = GovernanceLimits(max_steps_per_run=500, max_cost_per_run_usd=10.0)
    # A forwarded budget tighter than base wins; a looser one does not loosen base.
    tightened = apply_budget_override(base, {"max_turns": 50, "max_cost_usd": 2.0})
    assert tightened.max_steps_per_run == 50
    assert tightened.max_cost_per_run_usd == 2.0

    looser = apply_budget_override(base, {"max_turns": 9000, "max_cost_usd": 999.0})
    assert looser.max_steps_per_run == 500  # base was stricter
    assert looser.max_cost_per_run_usd == 10.0


def test_apply_budget_override_sets_a_dimension_base_left_unbounded() -> None:
    base = GovernanceLimits(max_steps_per_run=None, max_cost_per_run_usd=None)
    out = apply_budget_override(base, {"max_turns": 30, "max_cost_usd": 1.5})
    assert out.max_steps_per_run == 30
    assert out.max_cost_per_run_usd == 1.5


def test_apply_budget_override_noop_on_empty() -> None:
    base = GovernanceLimits(max_steps_per_run=500)
    assert apply_budget_override(base, None) is base
    assert apply_budget_override(base, {}) is base


def test_remaining_budget_is_envelope_minus_spent() -> None:
    tracker = CircuitBreakerTracker(
        GovernanceLimits(max_steps_per_run=10, max_cost_per_run_usd=5.0)
    )
    tracker.add_cost(2.0)
    tracker.check_agent_step("a")
    tracker.check_agent_step("a")  # 2 steps spent
    rem = tracker.remaining_budget()
    assert rem["max_turns"] == 8
    assert rem["max_cost_usd"] == 3.0


def test_remaining_budget_empty_when_unbounded() -> None:
    tracker = CircuitBreakerTracker(
        GovernanceLimits(max_steps_per_run=None, max_cost_per_run_usd=None)
    )
    assert tracker.remaining_budget() == {}


def test_remaining_budget_floors_at_zero_when_overspent() -> None:
    tracker = CircuitBreakerTracker(GovernanceLimits(max_cost_per_run_usd=1.0))
    # add_cost raises past the limit, so set the internal total directly to simulate "already over".
    tracker._total_cost_usd = 5.0
    assert tracker.remaining_budget()["max_cost_usd"] == 0.0
