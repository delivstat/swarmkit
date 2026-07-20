"""StageRunner — a bounded, deterministic stage sequence for one requirement.

The agent-determination-only shape (design/details/sdlc-pipeline-example.md, and
`feedback_llm_language_code_doing`): **deterministic code sequences the stages**;
agents only produce artifacts and verdicts. Each stage runs one agent to draft its
artifact; a stage whose agent carries a ``funnel`` runs the gate (judge -> multi-party
approve, retry re-runs the agent) and blocks the sequence until the human signs off.

IAM scoping is enforced per stage through the ``GovernanceProvider``: an agent may only
exercise the scopes it holds, so an OMS agent is denied a Web-app resource by
construction. Every stage transition is recorded on the append-only audit log,
correlated by ``requirement_id``.

This is the bounded precursor to the slice-5 controller (data-driven stage-graph); the
controller generalises this sequencing, it does not replace the gate mechanics here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from swarmkit_runtime.governance import AuditEvent
from swarmkit_runtime.resolver import ResolvedAgent

from ._gate_funnel import run_agent_funnel_gate

# Runs one agent for its determination: (agent, input, critique) -> artifact text.
# `critique` is None on the first pass and carries the gate's feedback on a retry.
AgentRunner = Callable[[ResolvedAgent, str, str | None], Awaitable[str]]


@dataclass(frozen=True)
class StageResult:
    agent_id: str
    artifact: str
    gated: bool
    outcome: str  # "produced" | "approved" | "rejected" | "denied"
    provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class StageRunResult:
    requirement_id: str
    status: str  # "completed" | "rejected" | "denied"
    stages: list[StageResult] = field(default_factory=list)
    detail: str = ""

    @property
    def artifacts(self) -> dict[str, str]:
        return {s.agent_id: s.artifact for s in self.stages}


class StageRunner:
    """Sequences a bounded stage chain for one requirement (see module docstring)."""

    def __init__(
        self,
        *,
        governance: Any,
        review_queue: Any,
        role_registry: Any,
        agent_runner: AgentRunner,
        **resolve_kwargs: Any,
    ) -> None:
        self._gov = governance
        self._queue = review_queue
        self._roles = role_registry
        self._run_agent = agent_runner
        self._resolve_kwargs = resolve_kwargs

    async def run(
        self,
        stages: Sequence[ResolvedAgent],
        *,
        requirement_id: str,
        initial_input: str = "",
    ) -> StageRunResult:
        results: list[StageResult] = []
        prior = initial_input

        for agent in stages:
            denial = await self._check_scope(agent, requirement_id)
            if denial is not None:
                results.append(denial)
                return StageRunResult(
                    requirement_id=requirement_id,
                    status="denied",
                    stages=results,
                    detail=f"stage {agent.id!r} denied by IAM scope",
                )

            async def produce(
                critique: str | None,
                _agent: ResolvedAgent = agent,
                _prior: str = prior,
            ) -> str:
                return await self._run_agent(_agent, _prior, critique)

            if agent.funnel is not None:
                stage_result = await self._run_gated_stage(agent, produce, requirement_id)
            else:
                artifact = await produce(None)
                await self._record(agent.id, requirement_id, "stage.produced", {"gated": False})
                stage_result = StageResult(
                    agent_id=agent.id, artifact=artifact, gated=False, outcome="produced"
                )

            results.append(stage_result)
            if stage_result.outcome == "rejected":
                return StageRunResult(
                    requirement_id=requirement_id,
                    status="rejected",
                    stages=results,
                    detail=f"stage {agent.id!r} rejected at its funnel gate",
                )
            prior = stage_result.artifact

        return StageRunResult(
            requirement_id=requirement_id, status="completed", stages=results
        )

    async def _run_gated_stage(
        self,
        agent: ResolvedAgent,
        produce: Callable[[str | None], Awaitable[str]],
        requirement_id: str,
    ) -> StageResult:
        assert agent.funnel is not None
        state = await run_agent_funnel_gate(
            dict(agent.funnel.spec),
            produce=produce,
            governance=self._gov,
            review_queue=self._queue,
            role_registry=self._roles,
            topology_id=requirement_id,
            agent_id=agent.id,
            gate_id=f"{requirement_id}:{agent.id}",
            **self._resolve_kwargs,
        )
        provenance = state.get("provenance", {})
        await self._record(
            agent.id,
            requirement_id,
            "stage.gated",
            {"outcome": state.get("outcome"), "retries": provenance.get("retries", 0)},
        )
        return StageResult(
            agent_id=agent.id,
            artifact=str(provenance.get("artifact") or ""),
            gated=True,
            outcome=str(state.get("outcome", "rejected")),
            provenance=provenance,
        )

    async def _check_scope(
        self, agent: ResolvedAgent, requirement_id: str
    ) -> StageResult | None:
        """Deny a stage whose agent lacks the scopes its work requires (IAM scoping).

        The agent's ``iam.base_scope`` is what it holds; ``iam.elevated_scopes`` (if any)
        is what this stage additionally needs. A required scope not held is denied through
        the policy engine — an OMS agent cannot reach a Web-app resource.
        """
        iam = agent.iam or {}
        held = frozenset(iam.get("base_scope", []) or [])
        required = frozenset(iam.get("elevated_scopes", []) or [])
        missing = required - held
        if not missing:
            return None
        decision = await self._gov.evaluate_action(
            agent_id=agent.id,
            action="stage.access",
            scopes_required=missing,
        )
        if decision.allowed:
            return None
        await self._record(
            agent.id, requirement_id, "stage.denied", {"missing_scopes": sorted(missing)}
        )
        return StageResult(
            agent_id=agent.id,
            artifact="",
            gated=bool(agent.funnel),
            outcome="denied",
        )

    async def _record(
        self, agent_id: str, requirement_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        await self._gov.record_event(
            AuditEvent(
                event_type=event_type,
                agent_id=agent_id,
                timestamp=datetime.now(tz=UTC),
                run_id=requirement_id,
                payload={"requirement_id": requirement_id, **payload},
            )
        )


__all__ = ["AgentRunner", "StageResult", "StageRunResult", "StageRunner"]
