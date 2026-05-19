"""Slack notification provider — incoming webhook with formatted messages."""

from __future__ import annotations

from typing import Any

import httpx

from swarmkit_runtime.notifications._provider import NotificationEvent, NotificationProvider


class SlackNotificationProvider(NotificationProvider):
    """Slack incoming webhook. Sends formatted messages."""

    provider_id = "slack"

    def __init__(self, webhook_url: str, channel: str | None = None) -> None:
        self._webhook_url = webhook_url
        self._channel = channel

    async def notify(self, event: NotificationEvent) -> bool:
        icon = {
            "hitl_requested": ":raised_hand:",
            "run_ended_error": ":x:",
            "skill_gap_surfaced": ":bulb:",
        }.get(event.event_type, ":bell:")
        text = (
            f"{icon} *{event.event_type}*\n{event.summary}\n"
            f"`run={event.run_id}` `topology={event.topology_id}`"
        )

        payload: dict[str, Any] = {"text": text}
        if self._channel:
            payload["channel"] = self._channel

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self._webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                return resp.status_code < 400
        except (httpx.HTTPError, OSError):
            return False
