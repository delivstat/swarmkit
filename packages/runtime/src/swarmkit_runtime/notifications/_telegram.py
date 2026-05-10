"""Telegram notification provider — Bot API with Markdown formatting."""

from __future__ import annotations

import httpx

from swarmkit_runtime.notifications._provider import NotificationEvent, NotificationProvider


class TelegramNotificationProvider(NotificationProvider):
    """Telegram Bot API. Sends messages to a chat via bot token."""

    provider_id = "telegram"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    async def notify(self, event: NotificationEvent) -> bool:
        icon = {
            "hitl_requested": "✋",
            "run_ended_error": "❌",
            "skill_gap_surfaced": "\U0001f4a1",
        }.get(event.event_type, "\U0001f514")

        text = (
            f"{icon} *{event.event_type}*\n"
            f"{event.summary}\n"
            f"`run={event.run_id}` `topology={event.topology_id}`"
        )

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code < 400
        except (httpx.HTTPError, OSError):
            return False
