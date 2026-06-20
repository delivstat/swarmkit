"""Scenario Studio — run imported custom detectors on the box (Phase 2 slice 3).

A background, on-demand loop: for each ARMED custom rule (condition_type:"custom"
referencing a dataset that has an imported model), grab a snapshot on a cadence, run
the trained detector (ultralytics, CPU) and fire a count/presence alert. The custom
model runs Minder-side — NOT as a Frigate detector — so the stock Frigate model
(person/car/dog/cat) stays intact. Inference only happens while a custom rule is armed
(no idle load); cadence-bounded so it never starves the 3B or Frigate.

No cloud here — the trained model runs fully local.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
RULES_FILE = DATA_DIR / "rules.json"
INTERVAL_S = int(os.environ.get("MINDER_CUSTOM_INTERVAL", "60"))
ENABLED = os.environ.get("MINDER_CUSTOM_DETECT", "on").lower() in ("on", "1", "true")

_state: dict = {}  # per-rule debounce/cooldown


def _cooldown_s() -> int:
    try:
        sys.path.insert(0, "/app/mcp-servers")
        from _alert_sink import ALERT_COOLDOWN_S

        return ALERT_COOLDOWN_S
    except Exception:
        return 300


def _fire_alert(msg: str, cam: str) -> None:
    sys.path.insert(0, "/app/mcp-servers")
    from _alert_sink import write_alert

    write_alert(msg, cam)


_OPS = {
    ">": lambda n, v: n > v,
    ">=": lambda n, v: n >= v,
    "<": lambda n, v: n < v,
    "<=": lambda n, v: n <= v,
    "==": lambda n, v: n == v,
}


def evaluate(rule: dict, count: int, now: float) -> bool:
    """Count threshold + debounce + cooldown + re-arm (mirrors the count matcher).
    Pure-ish: mutates _state. Default op '>' value 0 = presence (any detection)."""
    op = rule.get("count_op", ">")
    val = rule.get("count_value", 0)
    fn = _OPS.get(op, _OPS[">"])
    key = f"custom|{rule.get('dataset')}|{rule.get('camera')}|{op}|{val}"
    st = _state.setdefault(key, {"since": None, "last": 0.0})
    if not fn(count, val):
        st["since"] = None
        return False
    if st["since"] is None:
        st["since"] = now
    if now - st["since"] < float(rule.get("debounce_s", 3)):
        return False
    if st["last"] and now - st["last"] < _cooldown_s():
        return False
    st["last"] = now
    return True


def _cycle(_ds=None) -> int:
    if not RULES_FILE.exists():
        return 0
    try:
        rules = json.loads(RULES_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        return 0
    armed = [r for r in rules if r.get("enabled", True) and r.get("condition_type") == "custom"]
    if not armed:
        return 0  # no inference unless something is armed
    ds = _ds
    if ds is None:
        import dataset as ds
    now = time.time()
    fired = 0
    for rule in armed:
        name = rule.get("dataset")
        if not name or not ds.has_model(name):
            continue
        cam = rule.get("camera") or ds._meta(name).get("camera", "")
        img = ds._grab_snapshot(cam)
        if not img:
            continue
        res = ds.detect_custom(name, img)
        if res.get("error"):
            continue
        if evaluate(rule, res.get("count", 0), now):
            label = (
                rule.get("condition") or f"{ds._meta(name).get('class_name', 'object')} on {cam}"
            )
            _fire_alert(f"{label}: {res['count']} detected", cam)
            fired += 1
    return fired


def _loop() -> None:
    while True:
        try:
            _cycle()
        except Exception as e:  # never let the loop die
            print(f"[custom] cycle failed: {e}", flush=True)
        time.sleep(INTERVAL_S)


def start_custom_detector() -> None:
    if not ENABLED:
        print("[custom] disabled (MINDER_CUSTOM_DETECT=off)", flush=True)
        return
    threading.Thread(target=_loop, daemon=True, name="custom-detector").start()
    print(f"[custom] detector loop started (every {INTERVAL_S}s, only when armed)", flush=True)
