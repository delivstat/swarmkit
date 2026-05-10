"""Built-in notification provider implementations."""

from __future__ import annotations

import sys
from typing import Any

import httpx

from swarmkit_runtime.notifications._provider import NotificationEvent, NotificationProvider


class TerminalNotificationProvider(NotificationProvider):
    """Prints notifications to stdout. For local development."""

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


class WebhookNotificationProvider(NotificationProvider):
    """Generic HTTP POST webhook. Sends JSON payload to configured URL."""

    provider_id = "webhook"

    def __init__(
        self, url: str, headers: dict[str, str] | None = None, timeout: float = 10.0
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._timeout = timeout

    async def notify(self, event: NotificationEvent) -> bool:
        payload = {
            "event_type": event.event_type,
            "run_id": event.run_id,
            "topology_id": event.topology_id,
            "summary": event.summary,
            "metadata": event.metadata,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    self._url,
                    json=payload,
                    headers={"Content-Type": "application/json", **self._headers},
                )
                return resp.status_code < 400
        except (httpx.HTTPError, OSError):
            return False


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


def build_provider(provider_type: str, config: dict[str, Any]) -> NotificationProvider:
    """Factory for notification providers from workspace config."""
    if provider_type == "terminal":
        return TerminalNotificationProvider()
    if provider_type == "webhook":
        return WebhookNotificationProvider(
            url=config["url"],
            headers=config.get("headers"),
            timeout=config.get("timeout", 10.0),
        )
    if provider_type == "slack":
        return SlackNotificationProvider(
            webhook_url=config["webhook_url"],
            channel=config.get("channel"),
        )
    msg = f"Unknown notification provider: {provider_type}. Available: terminal, webhook, slack."
    raise ValueError(msg)
