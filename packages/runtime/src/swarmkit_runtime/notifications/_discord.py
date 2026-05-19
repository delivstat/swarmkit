"""Discord notification provider — webhook with color-coded embeds."""

from __future__ import annotations

from typing import Any

import httpx

from swarmkit_runtime.notifications._provider import NotificationEvent, NotificationProvider


class DiscordNotificationProvider(NotificationProvider):
    """Discord webhook. Sends formatted embed messages."""

    provider_id = "discord"

    def __init__(self, webhook_url: str, username: str = "SwarmKit") -> None:
        self._webhook_url = webhook_url
        self._username = username

    async def notify(self, event: NotificationEvent) -> bool:
        color = {
            "hitl_requested": 0xFFA500,
            "run_ended_error": 0xFF0000,
            "skill_gap_surfaced": 0x00BFFF,
        }.get(event.event_type, 0x808080)

        payload: dict[str, Any] = {
            "username": self._username,
            "embeds": [
                {
                    "title": event.event_type,
                    "description": event.summary,
                    "color": color,
                    "fields": [
                        {"name": "Run", "value": event.run_id, "inline": True},
                        {"name": "Topology", "value": event.topology_id, "inline": True},
                    ],
                }
            ],
        }
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
