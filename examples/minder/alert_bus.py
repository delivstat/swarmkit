"""Shared MQTT alert subscriber for channel adapters.

Each adapter is a DURABLE, persistent-session consumer of the `minder/alerts`
topic (fixed client_id, clean_session=False, QoS 1) — mosquitto keeps a
per-adapter queue while the adapter is down and redelivers on reconnect (the
topic -> durable-queue-per-consumer pattern). paho runs its own network thread;
each received alert is bridged onto the adapter's asyncio loop and handed to the
adapter's async deliver callback. write_alert (mcp-servers/_alert_sink.py) is the
publisher.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

ALERTS_TOPIC = "minder/alerts"
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))

log = logging.getLogger("minder-alertbus")


def start_alert_subscriber(
    client_id: str,
    loop: asyncio.AbstractEventLoop,
    on_alert: Callable[[dict], Awaitable[None]],
) -> Any:
    """Subscribe to minder/alerts as a durable persistent session and call
    `on_alert(alert)` on `loop` for each. Returns the connected paho client
    (loop_start'd, auto-reconnecting). No-op-safe if paho is missing."""
    import paho.mqtt.client as mqtt

    def _on_connect(client, userdata, flags, reason_code, properties=None) -> None:
        client.subscribe(ALERTS_TOPIC, qos=1)
        log.info("[alertbus] %s connected; subscribed %s", client_id, ALERTS_TOPIC)

    def _on_message(client, userdata, msg) -> None:
        try:
            alert = json.loads(msg.payload.decode())
        except Exception:
            return
        # bridge paho's thread -> the adapter's asyncio loop
        asyncio.run_coroutine_threadsafe(on_alert(alert), loop)

    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=False
        )
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id=client_id, clean_session=False)  # paho < 2
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client
