"""Generic webhook notification provider — HTTP POST with JSON payload."""

from __future__ import annotations

import httpx

from swarmkit_runtime.notifications._provider import NotificationEvent, NotificationProvider


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
