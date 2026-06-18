"""Active health monitor — periodic liveness/reachability probes (report-only).

Layered on the supervisor (which restarts CRASHED processes) and minder_ops.health()
(files/HA/Frigate). This catches the mode neither sees — "up but not working"
(hung, or a dependency/channel unreachable) — and REPORTS it. v1 never restarts
anything: a busy-but-alive service (e.g. swarmkit mid-inference) must not be killed.

Each cycle writes a snapshot to /data/health.json (served by GET /api/ops/health)
and, on a state TRANSITION, publishes an alert via the MQTT bus — so it fans out
to whatever channel is up and shows on the dashboard (a "Telegram blocked" warning
can't be delivered over Telegram).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/mcp-servers")

DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
HEALTH_FILE = DATA_DIR / "health.json"
PROBE_INTERVAL_S = int(os.environ.get("MINDER_HEALTH_INTERVAL", "45"))
FAIL_THRESHOLD = int(os.environ.get("MINDER_HEALTH_FAILS", "2"))  # consecutive fails -> down
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
HA_URL = os.environ.get("HA_URL", "http://localhost:8123").rstrip("/")
FRIGATE_URL = os.environ.get("FRIGATE_URL", "http://localhost:5000").rstrip("/")
POLL_INTERVAL_S = int(os.environ.get("MINDER_POLL_INTERVAL", "60"))


# ---- probe primitives ----


def _tcp(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _http_ok(url: str, timeout: float = 4.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # reachable — a 401/4xx still means the service is up
    except Exception:
        return False


def _proc_alive(needle: str) -> bool:
    for d in Path("/proc").glob("[0-9]*"):
        try:
            if needle in (d / "cmdline").read_bytes().decode("utf-8", "ignore"):
                return True
        except Exception:
            continue
    return False


def _channel_token(cid: str) -> str:
    try:
        from alert_bus import channel_token

        return channel_token(cid)
    except Exception:
        return ""


def _adapter_state(cid: str, proc_needle: str) -> tuple[str, str]:
    """An adapter is: off (no token configured), ok (configured + process alive),
    or down (configured but its process isn't running)."""
    if not _channel_token(cid):
        return "off", "not configured"
    if _proc_alive(proc_needle):
        return "ok", "running"
    return "down", "configured but the adapter process isn't running"


def _poll_fresh() -> tuple[str, str]:
    cursor = DATA_DIR / "frigate_cursor.json"
    if not cursor.exists():
        return "off", "no poll cycle yet"
    age = time.time() - cursor.stat().st_mtime
    if age < POLL_INTERVAL_S * 3:
        return "ok", f"last poll {int(age)}s ago"
    return "degraded", f"stalled — last poll {int(age)}s ago"


def _probe() -> list[dict]:
    """Run every component check once. (state, detail) per component; 'down' here
    is a single-cycle raw result — hysteresis is applied by the loop."""
    ha_state = ("ok", "reachable") if _http_ok(f"{HA_URL}/") else ("down", "unreachable")
    checks = [
        ("swarmkit", "SwarmKit serve", "ok" if _tcp("localhost", 8321) else "down", ":8321"),
        ("mosquitto", "MQTT broker", "ok" if _tcp("localhost", 1883) else "down", ":1883"),
        (
            "ollama",
            "Ollama (LLM/VLM)",
            "ok" if _http_ok(f"{OLLAMA_URL}/api/tags") else "down",
            ":11434",
        ),
        ("home_assistant", "Home Assistant", *ha_state),
        ("frigate", "Frigate", "ok" if _http_ok(f"{FRIGATE_URL}/api/version") else "down", ":5000"),
    ]
    comps = [{"id": c[0], "name": c[1], "state": c[2], "detail": c[3]} for c in checks]
    for cid, name, needle in (
        ("telegram", "Telegram", "/app/bot.py"),
        ("discord", "Discord", "/app/discord_bot.py"),
    ):
        st, detail = _adapter_state(cid, needle)
        comps.append({"id": cid, "name": name, "state": st, "detail": detail})
    st, detail = _poll_fresh()
    comps.append({"id": "poll", "name": "Monitoring poll", "state": st, "detail": detail})
    return comps


# ---- loop: hysteresis + snapshot + transition alerts ----

_fails: dict[str, int] = {}


def _apply_hysteresis(comps: list[dict], prev: dict[str, str]) -> None:
    """Only flip a component to 'down' after FAIL_THRESHOLD consecutive raw
    failures — a single transient blip stays at its previous state."""
    for c in comps:
        if c["state"] == "down":
            _fails[c["id"]] = _fails.get(c["id"], 0) + 1
            if _fails[c["id"]] < FAIL_THRESHOLD:
                c["state"] = prev.get(c["id"], "ok")  # not down yet — hold prior
        else:
            _fails[c["id"]] = 0


def _alert_transitions(comps: list[dict], prev: dict[str, str]) -> None:
    try:
        from _alert_sink import write_alert
    except Exception:
        return
    for c in comps:
        was, now = prev.get(c["id"]), c["state"]
        if was is None or was == now:
            continue
        if now == "down":
            write_alert(f"🩺 {c['name']} is DOWN — {c['detail']}", c["name"])
        elif was == "down" and now == "ok":
            write_alert(f"✅ {c['name']} recovered", c["name"])


def _cycle() -> dict:
    prev_snap = {}
    if HEALTH_FILE.exists():
        try:
            prev_snap = json.loads(HEALTH_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            prev_snap = {}
    prev = {c["id"]: c["state"] for c in prev_snap.get("components", [])}

    comps = _probe()
    _apply_hysteresis(comps, prev)
    _alert_transitions(comps, prev)

    snap = {
        "checked_at": time.time(),
        "components": comps,
        "healthy": all(c["state"] != "down" for c in comps),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(snap))
    return snap


def _loop() -> None:
    while True:
        try:
            _cycle()
        except Exception as e:
            print(f"[health] cycle failed: {e}", flush=True)
        time.sleep(PROBE_INTERVAL_S)


def start_health_monitor() -> None:
    """Start the report-only health checker in a background daemon thread."""
    threading.Thread(target=_loop, daemon=True, name="health-monitor").start()
    print(f"[health] monitor started (every {PROBE_INTERVAL_S}s, report-only)", flush=True)
