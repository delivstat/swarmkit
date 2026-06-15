"""Deterministic per-minute Frigate event poll — the reconcile backstop.

The real-time alert path is the MQTT subscriber (mqtt_listener); this is the
backstop that catches events missed while MQTT was disconnected. `poll_events`
is fully deterministic — it checks Frigate, matches events against rules.json,
and fires alerts as side-effects (shared dedup/cooldown state with the MQTT
path, so they never double-fire). There is no language task here, so there is
no LLM in the loop: this replaces the old `minder-poll` topology + `poll-frigate`
cron trigger, which wrapped this one deterministic tool call in a 3B agent that
looped until the tool-call cap and synthesised an answer nobody read. Pure code.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from types import ModuleType

POLL_INTERVAL_S = int(os.environ.get("MINDER_POLL_INTERVAL", "60"))
POLL_ENABLED = os.environ.get("MINDER_POLL", "on").lower() in ("on", "1", "true")

_frigate: ModuleType | None = None


def _frigate_mod() -> ModuleType:
    """Import the frigate MCP server module for its deterministic poll logic
    (same module the MQTT subscriber uses — one match/fire/dedup implementation)."""
    global _frigate
    if _frigate is None:
        sys.path.insert(0, "/app/mcp-servers")
        sys.path.insert(0, "/app/mcp-servers/frigate")
        import server as f

        _frigate = f
    return _frigate


def _poll_once() -> dict | None:
    """One deterministic poll cycle: call poll_events, parse, and log only real
    activity or errors (no per-minute heartbeat). Returns the parsed result, or
    None if the cycle failed (never raises — the backstop must keep running)."""
    try:
        res = json.loads(_frigate_mod().poll_events())
    except Exception as e:
        print(f"[poll] cycle failed: {e}", flush=True)
        return None
    if res.get("status") == "error":
        print(f"[poll] error: {res.get('message')}", flush=True)
    elif res.get("alerts") or res.get("time_rules_fired"):
        print(
            f"[poll] {res.get('alerts', 0)} alert(s), "
            f"{res.get('time_rules_fired', 0)} time-rule(s) from "
            f"{res.get('events_seen', 0)} event(s)",
            flush=True,
        )
    return res


def _loop() -> None:
    while True:
        time.sleep(POLL_INTERVAL_S)
        _poll_once()


def start_frigate_poller() -> None:
    """Start the backstop poll loop in a background daemon thread. No-op if
    MINDER_POLL is off."""
    if not POLL_ENABLED:
        print("[poll] disabled (MINDER_POLL=off)", flush=True)
        return
    threading.Thread(target=_loop, daemon=True, name="frigate-poller").start()
    print(f"[poll] deterministic backstop started (every {POLL_INTERVAL_S}s, no LLM)", flush=True)
