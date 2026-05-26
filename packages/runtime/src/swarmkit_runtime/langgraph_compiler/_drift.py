"""Intent-drift observation and response.

Creates drift observers for agents and handles drift results
(logging, warning, or nudge injection).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.model_providers import Message
from swarmkit_runtime.resolver import ResolvedAgent
from swarmkit_runtime.telemetry._metrics import record_drift_breach, record_drift_score

# Prefixes that indicate error passthroughs rather than agent reasoning.
# Drift scoring is skipped for these outputs.
_ERROR_PREFIXES = ("Error:", "Tool error:", "ToolError:")

_ATTR_PREFIX = "swarmkit"


def _create_drift_observer(agent: ResolvedAgent) -> Any:
    """Create an IntentObserver for the agent if monitoring is configured."""
    from swarmkit_runtime.drift import IntentMonitoringConfig, IntentObserver  # noqa: PLC0415

    raw_config = getattr(agent, "intent_monitoring", None)
    if raw_config is None:
        return None

    if isinstance(raw_config, dict):
        config = IntentMonitoringConfig.from_dict(raw_config)
    else:
        config = IntentMonitoringConfig.from_dict(
            {
                "enabled": getattr(raw_config, "enabled", False),
                "threshold": getattr(raw_config, "threshold", 0.75),
                "on_drift": getattr(raw_config, "on_drift", "log"),
            }
        )

    if not config.enabled:
        return None
    return IntentObserver(config)


def _is_error_passthrough(output: str) -> bool:
    """Return True if *output* looks like an error passthrough, not agent reasoning.

    Tool errors and system error messages are not representative of the
    agent's reasoning and should not be scored for intent drift.
    """
    stripped = output.lstrip()
    return any(stripped.startswith(prefix) for prefix in _ERROR_PREFIXES)


async def _handle_drift_result(
    drift_result: Any,
    observer: Any,
    governance: GovernanceProvider,
    agent_id: str,
    messages: list[Message],
) -> None:
    """Record drift and apply strategy (log/warn/nudge)."""
    # --- audit event (governance) ---
    await governance.record_event(
        AuditEvent(
            event_type="intent.drift",
            agent_id=agent_id,
            timestamp=datetime.now(tz=UTC),
            payload={
                "drift_score": drift_result.score,
                "threshold": drift_result.threshold,
                "exceeded": drift_result.exceeded,
                "action": drift_result.action_taken,
            },
        )
    )

    # --- OTel span event ---
    current_span = trace.get_current_span()
    current_span.add_event(
        "intent.drift",
        attributes={
            f"{_ATTR_PREFIX}.drift.score": drift_result.score,
            f"{_ATTR_PREFIX}.drift.threshold": drift_result.threshold,
            f"{_ATTR_PREFIX}.drift.action": drift_result.action_taken or "",
            f"{_ATTR_PREFIX}.drift.exceeded": drift_result.exceeded,
        },
    )

    # --- OTel metrics ---
    record_drift_score(agent_id=agent_id, score=drift_result.score)
    if drift_result.exceeded:
        record_drift_breach(agent_id=agent_id)

    # --- strategy actions ---
    if drift_result.exceeded and drift_result.action_taken == "nudge":
        nudge_msg = observer.get_nudge_message()
        messages.append(Message(role="user", content=nudge_msg))
        if os.environ.get("SWARMKIT_VERBOSE"):
            print(
                f"  [drift] score={drift_result.score:.4f} "
                f"threshold={drift_result.threshold} → nudge injected",
                file=sys.stderr,
            )
    elif drift_result.exceeded and os.environ.get("SWARMKIT_VERBOSE"):
        print(
            f"  [drift] score={drift_result.score:.4f} "
            f"threshold={drift_result.threshold} → {drift_result.action_taken}",
            file=sys.stderr,
        )
