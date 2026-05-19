"""Intent-drift observation and response.

Creates drift observers for agents and handles drift results
(logging, warning, or nudge injection).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from typing import Any

from swarmkit_runtime.governance import AuditEvent, GovernanceProvider
from swarmkit_runtime.model_providers import Message
from swarmkit_runtime.resolver import ResolvedAgent


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


async def _handle_drift_result(
    drift_result: Any,
    observer: Any,
    governance: GovernanceProvider,
    agent_id: str,
    messages: list[Message],
) -> None:
    """Record drift and apply strategy (log/warn/nudge)."""
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
