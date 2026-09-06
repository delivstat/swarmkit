"""Pushing events to an application, best-effort.

The contract, stated rather than implied:

* **At-most-once.** A webhook is retried a small fixed number of times and then dropped. There is
  no queue, no dead-letter, no backpressure — putting those in the runtime is the thing
  `extracting-the-channels.md` decided not to own.
* **A drop is recoverable.** Every event carries a cursor, and `GET /events?after=` replays from
  the audit log. An application that cares reconciles on startup; one that does not, does not.
* **A sink never fails a run.** A webhook that times out must not turn a successful run into a
  failed one. The application's outage is not the swarm's problem.

An application needing stronger delivery puts a queue between itself and the webhook, which is
where a queue belongs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger("swarmkit.events")

#: Small and fixed. A long retry schedule is a queue with extra steps, and the durable pull is
#: what makes a drop survivable.
MAX_ATTEMPTS = 3
BACKOFF_S = (0.5, 2.0)
TIMEOUT_S = 10.0


@runtime_checkable
class EventSink(Protocol):
    async def deliver(self, event: dict[str, Any]) -> bool: ...


@dataclass
class StdoutSink:
    """Prints the event. The honest default for development, and a working example of the shape."""

    async def deliver(self, event: dict[str, Any]) -> bool:
        print(json.dumps(event), file=sys.stdout, flush=True)
        return True


@dataclass
class WebhookSink:
    """POSTs the event as JSON."""

    url: str
    token: str = ""

    async def deliver(self, event: dict[str, Any]) -> bool:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                    resp = await client.post(self.url, json=event, headers=headers)
                if resp.status_code < 400:
                    return True
                # 4xx is the application rejecting the event, not a transport failure. Retrying a
                # 400 just sends the same rejected body twice.
                if resp.status_code < 500:
                    logger.warning(
                        "event sink %s rejected %s with %s",
                        self.url,
                        event.get("event_type"),
                        resp.status_code,
                    )
                    return False
            except httpx.HTTPError as exc:
                logger.debug("event sink %s attempt %d failed: %s", self.url, attempt + 1, exc)
            if attempt < len(BACKOFF_S):
                await asyncio.sleep(BACKOFF_S[attempt])
        logger.warning(
            "event sink %s did not accept %s after %d attempts — the application can recover it "
            "from GET /events?after=<its last cursor>",
            self.url,
            event.get("event_type"),
            MAX_ATTEMPTS,
        )
        return False


def build_sink(spec: dict[str, Any], token: str = "") -> EventSink:
    kind = str(spec.get("sink", ""))
    if kind == "stdout":
        return StdoutSink()
    if kind == "webhook":
        url = str(spec.get("url", ""))
        if not url:
            msg = "an events webhook sink needs a `url`"
            raise ValueError(msg)
        return WebhookSink(url=url, token=token)
    msg = f"unknown event sink {kind!r}. Available: webhook, stdout."
    raise ValueError(msg)


async def fan_out(sinks: list[EventSink], event: dict[str, Any]) -> None:
    """Deliver to every sink. A sink that raises is logged and skipped, never propagated.

    The run is the point; telling somebody about it is not worth failing it for.
    """
    for sink in sinks:
        try:
            await sink.deliver(event)
        except Exception:
            logger.warning("event sink %r raised", type(sink).__name__, exc_info=True)
