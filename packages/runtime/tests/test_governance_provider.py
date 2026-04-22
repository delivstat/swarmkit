"""Tests for the GovernanceProvider ABC and MockGovernanceProvider (M2).

See ``design/details/governance-provider-interface.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from swarmkit_runtime.governance import (
    AgentCredential,
    AuditEvent,
    GovernanceProvider,
    PolicyDecision,
)
from swarmkit_runtime.governance._mock import MockGovernanceProvider

# ---- MockGovernanceProvider: evaluate_action ----------------------------


@pytest.mark.asyncio
async def test_mock_allows_when_scopes_match() -> None:
    gov = MockGovernanceProvider(allowed_scopes=frozenset({"repo:read", "repo:write"}))
    decision = await gov.evaluate_action(
        agent_id="worker-1",
        action="invoke-skill",
        scopes_required=frozenset({"repo:read"}),
    )
    assert decision.allowed is True
    assert decision.tier == 1
    assert decision.scopes_granted == frozenset({"repo:read"})
    assert decision.scopes_denied == frozenset()


@pytest.mark.asyncio
async def test_mock_denies_when_scopes_missing() -> None:
    gov = MockGovernanceProvider(allowed_scopes=frozenset({"repo:read"}))
    decision = await gov.evaluate_action(
        agent_id="worker-1",
        action="invoke-skill",
        scopes_required=frozenset({"repo:read", "repo:write"}),
    )
    assert decision.allowed is False
    assert decision.scopes_denied == frozenset({"repo:write"})
    assert decision.scopes_granted == frozenset({"repo:read"})
    assert "repo:write" in decision.reason


@pytest.mark.asyncio
async def test_mock_denies_with_no_allowed_scopes() -> None:
    gov = MockGovernanceProvider()
    decision = await gov.evaluate_action(
        agent_id="worker-1",
        action="invoke-skill",
        scopes_required=frozenset({"anything"}),
    )
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_mock_allows_empty_required_scopes() -> None:
    gov = MockGovernanceProvider()
    decision = await gov.evaluate_action(
        agent_id="worker-1",
        action="invoke-skill",
        scopes_required=frozenset(),
    )
    assert decision.allowed is True


# ---- MockGovernanceProvider: audit events --------------------------------


@pytest.mark.asyncio
async def test_mock_collects_audit_events() -> None:
    gov = MockGovernanceProvider()
    event = AuditEvent(
        event_type="skill.invoked",
        agent_id="worker-1",
        timestamp=datetime(2026, 4, 22, tzinfo=UTC),
        payload={"skill": "say-hello"},
        topology_id="hello",
        skill_id="say-hello",
    )
    await gov.record_event(event)
    await gov.record_event(event)

    assert len(gov.events) == 2
    assert gov.events[0].event_type == "skill.invoked"
    assert gov.events[0].skill_id == "say-hello"


@pytest.mark.asyncio
async def test_mock_events_returns_copy() -> None:
    """The events list is a copy — mutating it doesn't affect the provider."""
    gov = MockGovernanceProvider()
    event = AuditEvent(
        event_type="test",
        agent_id="a",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await gov.record_event(event)
    events_copy = gov.events
    events_copy.clear()
    assert len(gov.events) == 1


# ---- MockGovernanceProvider: identity ------------------------------------


@pytest.mark.asyncio
async def test_mock_verifies_identity() -> None:
    gov = MockGovernanceProvider()
    result = await gov.verify_identity(
        agent_id="worker-1",
        credential=AgentCredential(credential_type="mock", value="test-token"),
    )
    assert result.verified is True
    assert result.agent_id == "worker-1"


# ---- MockGovernanceProvider: trust scores --------------------------------


@pytest.mark.asyncio
async def test_mock_returns_configured_trust_score() -> None:
    gov = MockGovernanceProvider(trust_scores={"worker-1": 0.5})
    score = await gov.get_trust_score(agent_id="worker-1")
    assert score.score == 0.5
    assert score.tier == "degraded"


@pytest.mark.asyncio
async def test_mock_returns_default_trust_for_unknown_agent() -> None:
    gov = MockGovernanceProvider()
    score = await gov.get_trust_score(agent_id="unknown")
    assert score.score == 1.0
    assert score.tier == "fully-trusted"


# ---- separation-of-powers invariants ------------------------------------


def test_governance_provider_is_abstract() -> None:
    """Cannot instantiate GovernanceProvider directly."""
    with pytest.raises(TypeError, match="abstract"):
        GovernanceProvider()  # type: ignore[abstract]


def test_mock_has_no_event_mutation_api() -> None:
    """The mock exposes no way to clear, delete, or modify audit events.

    This is a structural invariant from §8.3 — the media pillar is
    append-only from the executive's perspective. The mock enforces this
    by having only `.events` (read-only copy) and no mutation methods.
    """
    gov = MockGovernanceProvider()
    assert not hasattr(gov, "clear_events")
    assert not hasattr(gov, "delete_event")
    assert not hasattr(gov, "update_event")


def test_policy_decision_is_frozen() -> None:
    """PolicyDecision is immutable — governance verdicts cannot be tampered with."""
    decision = PolicyDecision(
        allowed=True,
        reason="test",
        tier=1,
        scopes_granted=frozenset({"x"}),
        scopes_denied=frozenset(),
    )
    with pytest.raises(AttributeError):
        decision.allowed = False  # type: ignore[misc]


def test_audit_event_is_frozen() -> None:
    """AuditEvent is immutable — recorded events cannot be altered."""
    event = AuditEvent(
        event_type="test",
        agent_id="a",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(AttributeError):
        event.event_type = "tampered"  # type: ignore[misc]


# ---- M2 exit demo scenario ---------------------------------------------


@pytest.mark.asyncio
async def test_exit_demo_deny_and_audit() -> None:
    """M2 exit demo (per IMPLEMENTATION-PLAN.md): a worker tries to invoke a
    skill it lacks scope for; policy denies; audit records the attempt.

    This test proves the GovernanceProvider ABC + MockGovernanceProvider
    are sufficient to model the full deny → audit → assert flow.
    """
    gov = MockGovernanceProvider(allowed_scopes=frozenset({"repo:read"}))

    # Worker tries to invoke a skill that requires write access.
    decision = await gov.evaluate_action(
        agent_id="worker-1",
        action="invoke-skill:deploy-to-prod",
        scopes_required=frozenset({"repo:read", "repo:write", "deploy:prod"}),
    )

    # Policy denies — missing repo:write and deploy:prod.
    assert decision.allowed is False
    assert decision.scopes_denied == frozenset({"repo:write", "deploy:prod"})

    # Audit the denial.
    await gov.record_event(
        AuditEvent(
            event_type="policy.denied",
            agent_id="worker-1",
            timestamp=datetime.now(tz=UTC),
            payload={
                "action": "invoke-skill:deploy-to-prod",
                "scopes_denied": sorted(decision.scopes_denied),
                "reason": decision.reason,
            },
            topology_id="deploy-swarm",
            skill_id="deploy-to-prod",
        )
    )

    # Verify audit recorded the attempt.
    assert len(gov.events) == 1
    assert gov.events[0].event_type == "policy.denied"
    assert gov.events[0].agent_id == "worker-1"
    denied = gov.events[0].payload["scopes_denied"]
    assert isinstance(denied, list)
    assert "repo:write" in denied
