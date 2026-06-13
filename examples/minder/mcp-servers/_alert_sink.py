"""Shared alert sink — the single writer for Minder alerts + dashboard events.

Both the camera server (YOLO path) and the frigate poller (Frigate path) write
through here so there is exactly one alert shape, one Telegram queue, and one
event log — regardless of which perception substrate detected the event.

Kept dependency-free (stdlib only, no FastMCP) so either MCP server can import
it without pulling in the other's module-level app.
"""

import datetime
import json
import os
import re
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, "/app/mcp-servers")
import contextlib

from _atomic import write_json_atomic

DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
MONITOR_STATE_FILE = DATA_DIR / "monitor_state.json"
ALERT_COOLDOWN_S = int(os.environ.get("MINDER_ALERT_COOLDOWN", "600"))
OPS_URL = os.environ.get("MINDER_API_URL", "http://localhost:80")
INTERNAL_TOKEN_FILE = DATA_DIR / "internal_token"


def write_alert(
    message: str,
    camera: str = "",
    snapshot_path: str = "",
    video_path: str = "",
    event_id: str | None = None,
) -> str:
    """Write one alert to the Telegram queue (pending_alerts.json) and the
    dashboard log (events.json), using pre-captured media paths. Media capture
    is the caller's job — the YOLO path captures fresh RTSP frames, the Frigate
    path passes Frigate's snapshot/clip. Returns the event id."""
    ts = datetime.datetime.now(datetime.UTC)
    event_id = event_id or uuid.uuid4().hex[:8]

    alert_file = DATA_DIR / "pending_alerts.json"
    alerts = []
    if alert_file.exists():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            alerts = json.loads(alert_file.read_text())
    alerts.append(
        {
            "message": message,
            "camera": camera,
            "timestamp": ts.isoformat(),
            "snapshot_path": snapshot_path,
            "video_path": video_path,
        }
    )
    write_json_atomic(alert_file, alerts)

    events_file = DATA_DIR / "events.json"
    events = []
    if events_file.exists():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            events = json.loads(events_file.read_text())
    events.insert(
        0,
        {
            "id": event_id,
            "timestamp": ts.isoformat(),
            "camera": camera,
            "condition": message,
            "message": message,
            "snapshot_path": snapshot_path,
            "video_path": video_path,
            "viewed": False,
        },
    )
    write_json_atomic(events_file, events[:500])
    return event_id


def schedule_active(schedule: str) -> bool:
    """True if a rule's schedule window is active now (container-local time)."""
    if not schedule or schedule == "always":
        return True
    now_t = datetime.datetime.now()
    hour = now_t.hour
    if schedule == "night":
        return hour >= 20 or hour < 6
    if schedule == "day":
        return 6 <= hour < 20
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", schedule)
    if m:
        now_min = hour * 60 + now_t.minute
        start = int(m[1]) * 60 + int(m[2])
        end = int(m[3]) * 60 + int(m[4])
        if start <= end:
            return start <= now_min < end
        return now_min >= start or now_min < end  # wraps midnight
    return False


def load_monitor_state() -> dict:
    if MONITOR_STATE_FILE.exists():
        try:
            return json.loads(MONITOR_STATE_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def save_monitor_state(state: dict) -> None:
    write_json_atomic(MONITOR_STATE_FILE, state)


def execute_device_action(device: str, action: str) -> str:
    """Run a scenario device action through the ops API (single source of truth
    for device matching + HA control)."""
    token = ""
    if INTERNAL_TOKEN_FILE.exists():
        token = INTERNAL_TOKEN_FILE.read_text().strip()
    phrase = ("turn on " if action == "turn_on" else "turn off ") + device
    req = urllib.request.Request(
        f"{OPS_URL}/api/ops/message",
        data=json.dumps({"text": phrase, "source": "monitor"}).encode(),
        headers={"Content-Type": "application/json", "X-Minder-Internal": token},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=20)
    return json.loads(resp.read()).get("text", "")
