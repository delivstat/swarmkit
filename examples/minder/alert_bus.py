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
from pathlib import Path
from typing import Any

ALERTS_TOPIC = "minder/alerts"
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "minder-config.json"

log = logging.getLogger("minder-alertbus")


def channel_token(channel_id: str) -> str:
    """Resolve a channel adapter's token, dashboard-first so enabling a provider
    in Settings takes effect with no container restart: minder-config.json
    `channels.<id>.token`, then the legacy top-level `telegram_token`, then the
    `MINDER_<ID>_TOKEN` env var."""
    cfg: dict = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            cfg = {}
    ch = (cfg.get("channels") or {}).get(channel_id) or {}
    if ch.get("token") and ch.get("enabled", True):
        return str(ch["token"])
    if channel_id == "telegram" and cfg.get("telegram_token"):
        return str(cfg["telegram_token"])
    return os.environ.get(f"MINDER_{channel_id.upper()}_TOKEN", "")


def wait_for_token(channel_id: str, interval: int = 15) -> str:
    """Block until the channel has a token, polling config. Lets the entrypoint
    always launch an adapter while it idles until the provider is configured in
    the dashboard — so enabling it needs no container restart."""
    import time

    logged = False
    while True:
        token = channel_token(channel_id)
        if token:
            return token
        if not logged:
            log.info("[%s] no token yet — waiting (configure in the dashboard)", channel_id)
            logged = True
        time.sleep(interval)


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
