"""The inbound event ingress: authorize, audit, deliver.

Lifted out of the pipeline routes when the bundled sequencer was removed
(`docs/notes/pipeline-deprecation.md`). It is not sequencing — it is the front door a webhook or an
MCP tool comes through to deliver a *correlated external event*, which is how an application-owned
orchestrator gets driven. The guardrails are the reason it survives intact: `advance` and `skip` are
operator acts gated on reserved scopes that no agent token can hold, and every attempt is audited
whether it was allowed or denied.

The signal sink is injected. Nothing here knows what consumes the event.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from swarmkit_runtime.governance import AuditEvent

if TYPE_CHECKING:
    from swarmkit_runtime.governance import GovernanceProvider
    from swarmkit_runtime.triggers import PipelineSignal

logger = logging.getLogger("swarmkit.server")


PipelineMode = Literal["emit", "advance", "skip"]
_OPERATOR_MODES: frozenset[str] = frozenset({"advance", "skip"})


class PipelineIngressError(Exception):
    """A guardrail outcome the ingress could not satisfy — carries the HTTP status the endpoint
    maps to (403 authorization denied, 503 signal sink unconfigured). The audit record, when one is
    warranted (the authorization decision), is written *before* this is raised."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _ingress_pipeline_event(
    *,
    governance: GovernanceProvider,
    signal: PipelineSignal | None,
    correlation_id: str,
    event: str,
    mode: PipelineMode,
    actor_identity: str,
    source: str,
    source_event_id: str | None,
) -> None:
    """The single, load-bearing ingress path shared by the HTTP endpoint and the MCP tool.

    Authorize → audit → deliver, in that order:

    1. **Authorize.** ``advance`` / ``skip`` are operator acts: the *caller's identity* must hold
       the matching reserved scope (``pipeline:advance`` / ``pipeline:skip``) via the
       GovernanceProvider — a human-identity act, structurally un-grantable to a transport/agent
       token (design §8.7). ``emit`` is authorised by the normal serve ``run`` tier and needs no
       governance grant.
    2. **Audit.** Every ingress attempt — allowed *or* denied — is recorded on the append-only
       audit, stamped with the ``source`` and ``(correlation_id, event, mode)`` and the pass-through
       ``source_event_id``, so "who advanced X, and why" is answerable.
    3. **Deliver.** Hand the ``(correlation_id, event)`` to the injected signal sink. Dedup and
       sequencing are the orchestrator's job; the runtime keeps no dedup state.

    Raises :class:`PipelineIngressError` (403) when authorization is denied — after auditing the
    denial — and (503) when the signal sink is unconfigured (sanctioned, like run-stage).
    """
    allowed = True
    reason = "emit authorised by the serve run tier"
    if mode in _OPERATOR_MODES:
        scope = f"pipeline:{mode}"
        decision = await governance.evaluate_action(
            agent_id=actor_identity,
            action=scope,
            scopes_required=frozenset({scope}),
            context={"source": source, "correlation_id": correlation_id, "event": event},
        )
        allowed = decision.allowed
        reason = decision.reason

    await governance.record_event(
        AuditEvent(
            event_type="pipeline.ingress",
            agent_id=actor_identity,
            timestamp=datetime.now(tz=UTC),
            payload={
                "correlation_id": correlation_id,
                "event": event,
                "mode": mode,
                "source": source,
                "source_event_id": source_event_id,
                "allowed": allowed,
                "reason": reason,
            },
            policy_decision="allow" if allowed else "deny",
            policy_reason=reason,
        )
    )

    if not allowed:
        raise PipelineIngressError(
            403,
            f"{source} is not authorised to {mode} pipeline event "
            f"{event!r} for {correlation_id!r}: {reason}",
        )

    if signal is None:
        raise PipelineIngressError(
            503,
            "pipeline signal seam not configured "
            "(set app.state.pipeline_signal to a PipelineSignal)",
        )

    await signal(correlation_id, event)
