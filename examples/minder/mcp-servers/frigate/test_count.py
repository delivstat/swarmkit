"""Standalone tests for Scenario Studio Phase 1 — count conditions.

A count rule fires when count(object on a camera) {op} value, held for debounce_s,
driven by Frigate's object-count MQTT topics. This pins the comparator, the debounce
(must hold before firing), the cooldown (no rapid re-fire), the re-arm (fires again
only after the condition clears), camera/object matching, and that count rules do NOT
also fire on the per-event presence path.

Run in-container:  docker compose exec -T minder python /app/mcp-servers/frigate/test_count.py
"""

import json
import tempfile
from pathlib import Path

import server as f


def _capture():
    alerts, devices = [], []
    f.write_alert = lambda msg, cam="", snap="", vid="": alerts.append((msg, cam))  # type: ignore
    f.execute_device_action = lambda d, a: devices.append((d, a)) or f"{a} {d}"  # type: ignore
    return alerts, devices


def test_comparator():
    assert f._count_compares(">", 4, 3) is True
    assert f._count_compares(">", 3, 3) is False
    assert f._count_compares(">=", 3, 3) is True
    assert f._count_compares("<", 2, 3) is True
    assert f._count_compares("<=", 3, 3) is True
    assert f._count_compares("==", 3, 3) is True
    print("ok  _count_compares handles > >= < <= ==")


def test_object_normalization():
    assert f._count_object_label("vehicle") == "car"
    assert f._count_object_label("car") == "car"
    assert f._count_object_label("people") == "person"
    print("ok  _count_object_label maps synonyms to canonical labels")


RULE = {"count_op": ">", "count_value": 3, "debounce_s": 5, "count_object": "car"}


def test_debounce_then_fire():
    f.ALERT_COOLDOWN_S = 600
    state: dict = {}
    assert f._eval_count_rule(RULE, 4, now=100.0, state=state) is False  # held 0s < 5
    assert f._eval_count_rule(RULE, 4, now=104.0, state=state) is False  # held 4s < 5
    assert f._eval_count_rule(RULE, 4, now=105.0, state=state) is True  # held 5s -> fire
    assert f._eval_count_rule(RULE, 4, now=106.0, state=state) is False  # cooldown blocks
    print("ok  fires only after the condition holds for debounce_s")


def test_rearm_after_clear():
    f.ALERT_COOLDOWN_S = 0  # isolate the re-arm path from cooldown
    state: dict = {}
    assert f._eval_count_rule(RULE, 5, now=1.0, state=state) is False  # debouncing
    assert f._eval_count_rule(RULE, 5, now=6.0, state=state) is True  # fires
    assert f._eval_count_rule(RULE, 1, now=7.0, state=state) is False  # below -> clears/re-arms
    assert f._eval_count_rule(RULE, 5, now=8.0, state=state) is False  # debouncing again
    assert f._eval_count_rule(RULE, 5, now=13.0, state=state) is True  # fires again
    print("ok  re-arms and fires again only after the count drops below the threshold")


def test_cooldown_blocks_rapid_refire():
    f.ALERT_COOLDOWN_S = 600
    r = {**RULE, "debounce_s": 0}
    state: dict = {}
    assert f._eval_count_rule(r, 9, now=10000.0, state=state) is True  # immediate (no debounce)
    assert f._eval_count_rule(r, 9, now=10001.0, state=state) is False  # within cooldown
    print("ok  cooldown blocks a second fire while the condition stays true")


def _write_rules(rules):
    tmp = Path(tempfile.mkdtemp()) / "rules.json"
    tmp.write_text(json.dumps(rules))
    f.RULES_FILE = tmp  # type: ignore


def test_handle_count_update_end_to_end():
    f.ALERT_COOLDOWN_S = 600
    f.POLLER_MODE = "live"
    f._slug_to_name = lambda: {"porch_1": "Porch-1"}  # type: ignore
    f.load_monitor_state = lambda: getattr(f, "_TEST_STATE", {})  # type: ignore
    f.save_monitor_state = lambda s: setattr(f, "_TEST_STATE", s)  # type: ignore
    f._TEST_STATE = {}  # type: ignore
    alerts, devices = _capture()
    _write_rules(
        [
            {
                "condition": "more than 3 cars on Porch-1",
                "cameras": ["Porch-1"],
                "condition_type": "count",
                "count_object": "car",
                "count_op": ">",
                "count_value": 3,
                "debounce_s": 0,
                "enabled": True,
                "actions": [
                    {"type": "device", "device": "Gate Light", "action": "turn_on"},
                    {"type": "alert"},
                ],
            }
        ]
    )
    # below threshold -> nothing
    assert f.handle_count_update("porch_1", "car", 2) is None
    # over threshold on the matching camera -> fires + runs the device action
    fired = f.handle_count_update("porch_1", "car", 4)
    assert fired and fired["count"] == 4, fired
    assert devices == [("Gate Light", "turn_on")], devices
    assert alerts and "Porch-1" in alerts[0][1], alerts
    # a different object on the same topic does not match this rule
    f._TEST_STATE = {}  # type: ignore
    assert f.handle_count_update("porch_1", "person", 9) is None
    # a different camera does not match
    assert f.handle_count_update("backyard", "car", 9) is None
    print("ok  handle_count_update fires on camera+object match, ignores others")


def test_count_rule_skipped_by_presence_matcher():
    # A count rule's condition text mentions "cars" -> _condition_to_labels would
    # match a car event; the event matcher must skip count rules so they don't
    # double-fire as presence.
    rule = {
        "condition": "more than 3 cars on Porch-1",
        "condition_type": "count",
        "cameras": ["Porch-1"],
        "count_object": "car",
    }
    ev = {"camera": "Porch-1", "label": "car", "event_id": "x", "source": "frigate"}
    assert f._match_and_fire_event(ev, [rule], {}, now=1.0, live=True) is None
    print("ok  presence matcher skips count rules (no double-fire)")


if __name__ == "__main__":
    test_comparator()
    test_object_normalization()
    test_debounce_then_fire()
    test_rearm_after_clear()
    test_cooldown_blocks_rapid_refire()
    test_handle_count_update_end_to_end()
    test_count_rule_skipped_by_presence_matcher()
    print("\nALL COUNT TESTS PASSED")
