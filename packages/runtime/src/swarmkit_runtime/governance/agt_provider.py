"""AGT-backed ``GovernanceProvider`` implementation (design §8.4, §16).

Wraps Microsoft AGT v3.x:
  - agent-os-kernel  → AsyncPolicyEvaluator for Tier 1 deterministic checks
  - agentmesh        → AgentIdentity for DIDs + Ed25519 + capability scopes
  - agent-control-plane → FlightRecorder for append-only, hash-chained audit

See ``design/details/governance-provider-interface.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_control_plane import FlightRecorder
from agent_os.policies import AsyncPolicyEvaluator, PolicyEvaluator
from agentmesh import AgentIdentity
from agentmesh.trust import TrustBridge

from . import (
    AgentCredential,
    AuditEvent,
    GovernanceProvider,
    IdentityVerification,
    PolicyDecision,
    TrustScore,
)

_DEFAULT_TRUST_SCORE = 500
_TRUST_SCALE = 1000


class AGTGovernanceProvider(GovernanceProvider):
    """v1.0 ``GovernanceProvider`` on top of Microsoft AGT.

    Constructor takes pre-built AGT objects so the caller controls
    lifecycle (policy directory, database path, identity registry).
    Factory classmethod ``from_workspace_config`` will handle the
    workspace.yaml → AGT wiring when the workspace loader is extended.
    """

    def __init__(
        self,
        *,
        policy_evaluator: AsyncPolicyEvaluator,
        flight_recorder: FlightRecorder,
        identity_registry: dict[str, AgentIdentity],
        trust_bridge: TrustBridge | None = None,
    ) -> None:
        self._policy = policy_evaluator
        self._recorder = flight_recorder
        self._identities = identity_registry
        self._trust = trust_bridge

    @classmethod
    def from_config(
        cls,
        *,
        policy_dir: str | Path,
        audit_db: str | Path = ".swarmkit/audit.db",
        identities: dict[str, AgentIdentity] | None = None,
    ) -> AGTGovernanceProvider:
        """Convenience factory: load policies from a directory, open the
        audit database, and optionally pre-register agent identities.
        """
        sync_evaluator = PolicyEvaluator()
        sync_evaluator.load_policies(str(policy_dir))
        async_evaluator = AsyncPolicyEvaluator(sync_evaluator)

        recorder = FlightRecorder(db_path=str(audit_db))

        return cls(
            policy_evaluator=async_evaluator,
            flight_recorder=recorder,
            identity_registry=identities or {},
        )

    # -- GovernanceProvider implementation --------------------------------

    async def evaluate_action(
        self,
        *,
        agent_id: str,
        action: str,
        scopes_required: frozenset[str],
        context: dict[str, object] | None = None,
    ) -> PolicyDecision:
        identity = self._identities.get(agent_id)
        granted: frozenset[str] = frozenset()
        denied: frozenset[str] = frozenset()

        if identity is not None:
            granted = frozenset(s for s in scopes_required if identity.has_capability(s))
            denied = scopes_required - granted
        else:
            denied = scopes_required

        if denied:
            return PolicyDecision(
                allowed=False,
                reason=f"agent '{agent_id}' lacks scopes: {sorted(denied)}",
                tier=1,
                scopes_granted=granted,
                scopes_denied=denied,
            )

        eval_context: dict[str, Any] = {
            "agent_id": agent_id,
            "action": action,
            **(context or {}),
        }
        agt_decision = await self._policy.evaluate(eval_context)

        return PolicyDecision(
            allowed=agt_decision.allowed,
            reason=agt_decision.reason,
            tier=1,
            scopes_granted=granted,
            scopes_denied=frozenset(),
        )

    async def verify_identity(
        self,
        *,
        agent_id: str,
        credential: AgentCredential,
    ) -> IdentityVerification:
        identity = self._identities.get(agent_id)
        if identity is None:
            return IdentityVerification(verified=False, agent_id=agent_id)

        if not identity.is_active():
            return IdentityVerification(verified=False, agent_id=agent_id)

        if credential.credential_type == "ed25519":
            try:
                verified = identity.verify_signature(
                    credential.value.encode("utf-8"),
                    credential.value,
                )
            except Exception:
                verified = False
            return IdentityVerification(verified=verified, agent_id=agent_id)

        return IdentityVerification(verified=identity.is_active(), agent_id=agent_id)

    async def record_event(self, event: AuditEvent) -> None:
        trace_id = self._recorder.start_trace(
            agent_id=event.agent_id,
            tool_name=event.skill_id or event.event_type,
            tool_args=dict(event.payload) if event.payload else None,
        )

        if "denied" in event.event_type or "violation" in event.event_type:
            self._recorder.log_violation(
                trace_id,
                violation_reason=str(event.payload.get("reason", event.event_type)),
            )
        elif "error" in event.event_type:
            self._recorder.log_error(
                trace_id,
                error=str(event.payload.get("error", event.event_type)),
            )
        else:
            self._recorder.log_success(trace_id, result=str(event.payload))

    async def get_trust_score(self, *, agent_id: str) -> TrustScore:
        if self._trust is None:
            return TrustScore(score=1.0, tier="fully-trusted")

        identity = self._identities.get(agent_id)
        if identity is None:
            return TrustScore(score=0.0, tier="unknown")

        peer = self._trust.get_peer(str(identity.did))
        if peer is None:
            return TrustScore(score=_DEFAULT_TRUST_SCORE / _TRUST_SCALE, tier="unverified")

        normalised = peer.trust_score / _TRUST_SCALE
        if normalised >= 0.8:
            tier = "fully-trusted"
        elif normalised >= 0.4:
            tier = "degraded"
        else:
            tier = "untrusted"
        return TrustScore(score=normalised, tier=tier)

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        """Flush and close the flight recorder."""
        self._recorder.flush()
        self._recorder.close()
