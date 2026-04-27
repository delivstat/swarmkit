"""Integration tests for AGTGovernanceProvider against real AGT v3.x.

These tests instantiate AGT's PolicyEvaluator, FlightRecorder, and
AgentIdentity — no mocks. They prove the thin layer translates correctly.
See ``design/details/governance-provider-interface.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from agentmesh import AgentIdentity
from swael_runtime.governance import (
    AgentCredential,
    AuditEvent,
)
from swael_runtime.governance.agt_provider import AGTGovernanceProvider


@pytest.fixture()
def policy_dir(tmp_path: Path) -> Path:
    """Write a minimal policy YAML that denies repo:write actions."""
    policy = {
        "name": "test-policy",
        "version": "1.0",
        "rules": [
            {
                "name": "deny-write",
                "condition": {"field": "action", "operator": "eq", "value": "skill:deploy"},
                "action": "deny",
                "priority": 10,
                "message": "Deployment requires elevated scope",
            },
        ],
    }
    policy_file = tmp_path / "deny-deploy.yaml"
    policy_file.write_text(yaml.dump(policy))
    return tmp_path


@pytest.fixture()
def audit_db(tmp_path: Path) -> Path:
    return tmp_path / "audit.db"


@pytest.fixture()
def worker_identity() -> AgentIdentity:
    return AgentIdentity.create(
        name="worker-1",
        sponsor="admin@swarmkit.dev",
        capabilities=["repo:read"],
    )


@pytest.fixture()
def provider(
    policy_dir: Path, audit_db: Path, worker_identity: AgentIdentity
) -> AGTGovernanceProvider:
    return AGTGovernanceProvider.from_config(
        policy_dir=policy_dir,
        audit_db=audit_db,
        identities={"worker-1": worker_identity},
    )


# ---- evaluate_action (scope checks + policy evaluation) ----------------


@pytest.mark.asyncio
async def test_agt_allows_when_scopes_and_policy_pass(provider: AGTGovernanceProvider) -> None:
    decision = await provider.evaluate_action(
        agent_id="worker-1",
        action="skill:read-repo",
        scopes_required=frozenset({"repo:read"}),
    )
    assert decision.allowed is True
    assert decision.tier == 1
    assert decision.scopes_granted == frozenset({"repo:read"})


@pytest.mark.asyncio
async def test_agt_denies_when_scope_missing(provider: AGTGovernanceProvider) -> None:
    decision = await provider.evaluate_action(
        agent_id="worker-1",
        action="skill:read-repo",
        scopes_required=frozenset({"repo:read", "repo:write"}),
    )
    assert decision.allowed is False
    assert decision.scopes_denied == frozenset({"repo:write"})
    assert "repo:write" in decision.reason


@pytest.mark.asyncio
async def test_agt_denies_when_policy_rejects(provider: AGTGovernanceProvider) -> None:
    """Scopes pass but the policy engine denies the action."""
    decision = await provider.evaluate_action(
        agent_id="worker-1",
        action="skill:deploy",
        scopes_required=frozenset({"repo:read"}),
    )
    assert decision.allowed is False
    assert "Deployment" in decision.reason or "deny" in decision.reason.lower()


@pytest.mark.asyncio
async def test_agt_denies_unknown_agent(provider: AGTGovernanceProvider) -> None:
    decision = await provider.evaluate_action(
        agent_id="unknown-agent",
        action="anything",
        scopes_required=frozenset({"repo:read"}),
    )
    assert decision.allowed is False
    assert decision.scopes_denied == frozenset({"repo:read"})


# ---- record_event (FlightRecorder audit) --------------------------------


@pytest.mark.asyncio
async def test_agt_records_violation_event(provider: AGTGovernanceProvider) -> None:
    await provider.record_event(
        AuditEvent(
            event_type="policy.denied",
            agent_id="worker-1",
            timestamp=datetime.now(tz=UTC),
            payload={"reason": "scope check failed", "action": "deploy"},
            topology_id="deploy-swarm",
            skill_id="deploy-to-prod",
        )
    )
    provider._recorder.flush()
    logs = provider._recorder.get_log()
    assert len(logs) >= 1
    last = logs[-1]
    assert last["agent_id"] == "worker-1"
    assert last["policy_verdict"] == "blocked"


@pytest.mark.asyncio
async def test_agt_records_success_event(provider: AGTGovernanceProvider) -> None:
    await provider.record_event(
        AuditEvent(
            event_type="skill.completed",
            agent_id="worker-1",
            timestamp=datetime.now(tz=UTC),
            payload={"result": "greeting sent"},
            skill_id="say-hello",
        )
    )
    provider._recorder.flush()
    logs = provider._recorder.get_log()
    assert len(logs) >= 1
    last = logs[-1]
    assert last["agent_id"] == "worker-1"
    assert last["policy_verdict"] == "allowed"


# ---- verify_identity ---------------------------------------------------


@pytest.mark.asyncio
async def test_agt_verifies_active_identity(provider: AGTGovernanceProvider) -> None:
    result = await provider.verify_identity(
        agent_id="worker-1",
        credential=AgentCredential(credential_type="did", value="test"),
    )
    assert result.verified is True
    assert result.agent_id == "worker-1"


@pytest.mark.asyncio
async def test_agt_rejects_unknown_agent_identity(provider: AGTGovernanceProvider) -> None:
    result = await provider.verify_identity(
        agent_id="unknown",
        credential=AgentCredential(credential_type="did", value="test"),
    )
    assert result.verified is False


# ---- get_trust_score ---------------------------------------------------


@pytest.mark.asyncio
async def test_agt_returns_default_trust_without_bridge(provider: AGTGovernanceProvider) -> None:
    score = await provider.get_trust_score(agent_id="worker-1")
    assert score.score == 1.0
    assert score.tier == "fully-trusted"


# ---- M2 exit demo (against real AGT) -----------------------------------


@pytest.mark.asyncio
async def test_agt_exit_demo_deny_and_audit(provider: AGTGovernanceProvider) -> None:
    """M2 exit demo with real AGT: worker requests scopes it doesn't have,
    policy denies, audit records the denial with a tamper-evident hash chain.
    """
    decision = await provider.evaluate_action(
        agent_id="worker-1",
        action="skill:deploy",
        scopes_required=frozenset({"repo:read", "deploy:prod"}),
    )
    assert decision.allowed is False
    assert "deploy:prod" in decision.scopes_denied

    await provider.record_event(
        AuditEvent(
            event_type="policy.denied",
            agent_id="worker-1",
            timestamp=datetime.now(tz=UTC),
            payload={
                "action": "skill:deploy",
                "scopes_denied": sorted(decision.scopes_denied),
                "reason": decision.reason,
            },
            topology_id="deploy-swarm",
            skill_id="deploy-to-prod",
        )
    )

    provider._recorder.flush()
    logs = provider._recorder.get_log()
    assert len(logs) >= 1
    last = logs[-1]
    assert last["agent_id"] == "worker-1"
    assert last["policy_verdict"] == "blocked"
    # FlightRecorder provides tamper-evident hash chain.
    assert "entry_hash" in last
