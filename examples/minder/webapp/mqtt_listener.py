"""Real-time camera alerting via Frigate's MQTT event stream.

Frigate publishes tracked-object events to mosquitto (topic ``frigate/events``).
This long-lived subscriber (started by the always-on webapp) runs the shared
rule-match + fire (``frigate.handle_live_event``) on each new/update event, so a
camera alert fires within ~1s instead of waiting up to a minute for the cron
poller. The poller stays as the reconcile backstop; both share the dedup/cooldown
state so they never double-fire.
"""

from __future__ import annotations

import json
import os
import sys
from types import ModuleType

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_ENABLED = os.environ.get("MINDER_MQTT", "on").lower() in ("on", "1", "true")

_frigate: ModuleType | None = None


def _frigate_mod() -> ModuleType:
    """Import the frigate MCP server module for its shared match/fire logic."""
    global _frigate
    if _frigate is None:
        sys.path.insert(0, "/app/mcp-servers")
        sys.path.insert(0, "/app/mcp-servers/frigate")
        import server as f

        _frigate = f
    return _frigate


def _on_connect(client, userdata, flags, reason_code, properties=None) -> None:
    client.subscribe("frigate/events")
    # Object-count topics (frigate/<camera|zone>/<object> = integer count) drive
    # Scenario Studio count conditions ("> 3 cars on the driveway"). frigate/+/+ is
    # 3-level so it never matches the 2-level frigate/events; non-count topics
    # (motion ON/OFF, etc.) are filtered by the integer-payload check below.
    client.subscribe("frigate/+/+")
    print(
        f"[mqtt] connected ({reason_code}); subscribed to frigate/events + frigate/+/+ (counts)",
        flush=True,
    )


def _on_count_message(topic: str, payload: bytes) -> None:
    """frigate/<camera|zone>/<object> with an integer payload -> a count update."""
    text = payload.decode("utf-8", "ignore").strip()
    if not (text.isdigit() or (text[:1] == "-" and text[1:].isdigit())):
        return  # not a count (motion ON/OFF, snapshot bytes, JSON, …)
    parts = topic.split("/")
    if len(parts) != 3:
        return
    _, source_key, obj = parts
    try:
        fired = _frigate_mod().handle_count_update(source_key, obj, int(text))
        if fired:
            print(f"[mqtt] count alert fired: {fired}", flush=True)
    except Exception as e:
        print(f"[mqtt] handle_count_update error: {e}", flush=True)


def _on_message(client, userdata, msg) -> None:
    if msg.topic != "frigate/events":
        _on_count_message(msg.topic, msg.payload)
        return
    try:
        payload = json.loads(msg.payload.decode())
    except Exception:
        return
    if payload.get("type") not in ("new", "update"):
        return
    after = payload.get("after") or {}
    try:
        fired = _frigate_mod().handle_live_event(after)
        if fired:
            print(f"[mqtt] alert fired: {fired}", flush=True)
    except Exception as e:
        print(f"[mqtt] handle_live_event error: {e}", flush=True)


def start_mqtt_listener() -> None:
    """Connect + subscribe in a background thread (auto-reconnecting). No-op if
    MINDER_MQTT is off or paho-mqtt isn't installed."""
    if not MQTT_ENABLED:
        print("[mqtt] disabled (MINDER_MQTT=off)", flush=True)
        return
    try:
        import paho.mqtt.client as mqtt
    except Exception as e:
        print(f"[mqtt] paho-mqtt unavailable, real-time alerts off: {e}", flush=True)
        return
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="minder-webapp")
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id="minder-webapp")  # paho < 2 fallback
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    try:
        client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()  # background thread; reconnects on its own
        print(f"[mqtt] listener started -> {MQTT_HOST}:{MQTT_PORT}", flush=True)
    except Exception as e:
        print(f"[mqtt] start failed (poller backstop still runs): {e}", flush=True)
