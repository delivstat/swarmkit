"""Terminal notification provider — prints to stderr for local dev."""

from __future__ import annotations

import sys

from swarmkit_runtime.notifications._provider import NotificationEvent, NotificationProvider


class TerminalNotificationProvider(NotificationProvider):
    """Prints notifications to stderr. For local development."""

    provider_id = "terminal"

    async def notify(self, event: NotificationEvent) -> bool:
        icon = {
            "hitl_requested": "[REVIEW]",
            "run_ended_error": "[ERROR]",
            "skill_gap_surfaced": "[GAP]",
        }.get(event.event_type, "[NOTIFY]")
        print(
            f"{icon} {event.summary} (run={event.run_id}, topology={event.topology_id})",
            file=sys.stderr,
        )
        return True
