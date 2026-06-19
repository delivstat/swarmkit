"""Standalone tests for Scenario Studio Phase 1.5 — zones (drawn regions).

Pins: the global zone key (camera-prefixed for MQTT uniqueness) + index; the Frigate
config generator emitting zones with flattened normalized coordinates; count rules
scoped to a zone firing only on that zone's count topic (and whole-frame rules
ignoring zone topics); and zone presence matching on a tracked event's current_zones.

Run in-container:  docker compose exec -T minder python /app/mcp-servers/frigate/test_zones.py
"""

import json
import tempfile
from pathlib import Path

import server as f

ZONES = {
    "Porch-1": [{"name": "driveway", "points": [[0.1, 0.2], [0.6, 0.2], [0.6, 0.9], [0.1, 0.9]]}]
}


def _capture():
    alerts, devices = [], []
    f.write_alert = lambda msg, cam="", snap="", vid="": alerts.append((msg, cam))  # type: ignore
    f.execute_device_action = lambda d, a: devices.append((d, a)) or f"{a} {d}"  # type: ignore
    return alerts, devices


def test_zone_key_and_index():
    assert f._zone_key("Porch-1", "driveway") == "porch_1__driveway"
    f._load_zones = lambda: ZONES  # type: ignore
    idx = f._zone_index()
    assert idx["porch_1__driveway"] == {"camera": "Porch-1", "name": "driveway"}
    print("ok  zone key is camera-prefixed + index maps key -> camera/name")


def test_build_config_includes_zones():
    f._load_zones = lambda: ZONES  # type: ignore
    cfg = f._build_config([{"name": "Porch-1", "ip": "192.168.0.101"}])
    zones = cfg["cameras"]["porch_1"]["zones"]
    assert "porch_1__driveway" in zones, zones
    assert (
        zones["porch_1__driveway"]["coordinates"]
        == "0.1000,0.2000,0.6000,0.2000,0.6000,0.9000,0.1000,0.9000"
    )
    print("ok  _build_config emits zones with flattened normalized coordinates")


def _write_rules(rules):
    tmp = Path(tempfile.mkdtemp()) / "rules.json"
    tmp.write_text(json.dumps(rules))
    f.RULES_FILE = tmp  # type: ignore


def _setup_count_env():
    f.ALERT_COOLDOWN_S = 600
    f.POLLER_MODE = "live"
    f._load_zones = lambda: ZONES  # type: ignore
    f._slug_to_name = lambda: {"porch_1": "Porch-1"}  # type: ignore
    f.load_monitor_state = lambda: getattr(f, "_TEST_STATE", {})  # type: ignore
    f.save_monitor_state = lambda s: setattr(f, "_TEST_STATE", s)  # type: ignore
    f._TEST_STATE = {}  # type: ignore


ZONE_COUNT_RULE = {
    "condition": "more than 3 car in driveway",
    "cameras": ["Porch-1"],
    "condition_type": "count",
    "count_object": "car",
    "count_op": ">",
    "count_value": 3,
    "debounce_s": 0,
    "zone": "driveway",
    "enabled": True,
    "actions": [{"type": "alert"}],
}


def test_count_in_zone_fires_only_on_its_zone_topic():
    _setup_count_env()
    _capture()
    _write_rules([ZONE_COUNT_RULE])
    # the zone's own count topic over threshold -> fires
    assert f.handle_count_update("porch_1__driveway", "car", 5) is not None
    # the WHOLE-FRAME camera topic must NOT fire a zone-scoped rule
    f._TEST_STATE = {}  # type: ignore
    assert f.handle_count_update("porch_1", "car", 5) is None
    # a different zone's topic must NOT fire it
    f._load_zones = lambda: {  # type: ignore
        **ZONES,
        "Backyard": [{"name": "lawn", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
    }
    f._slug_to_name = lambda: {"porch_1": "Porch-1", "backyard": "Backyard"}  # type: ignore
    f._TEST_STATE = {}  # type: ignore
    assert f.handle_count_update("backyard__lawn", "car", 9) is None
    print("ok  zone count rule fires only on its own zone's count topic")


def test_whole_frame_rule_ignores_zone_topic():
    _setup_count_env()
    _capture()
    whole = {**ZONE_COUNT_RULE, "zone": "", "condition": "more than 3 car on Porch-1"}
    _write_rules([whole])
    # whole-frame camera topic -> fires
    assert f.handle_count_update("porch_1", "car", 5) is not None
    # the zone topic must NOT also fire the whole-frame rule (no double-count)
    f._TEST_STATE = {}  # type: ignore
    assert f.handle_count_update("porch_1__driveway", "car", 5) is None
    print("ok  whole-frame count rule ignores zone count topics")


def test_presence_in_zone():
    f._load_zones = lambda: ZONES  # type: ignore
    f.ALERT_COOLDOWN_S = 600
    rule = {
        "condition": "person in driveway",
        "cameras": ["Porch-1"],
        "zone": "driveway",
        "enabled": True,
    }
    inside = {
        "camera": "Porch-1",
        "label": "person",
        "zone": "porch_1__driveway",
        "event_id": "a",
        "source": "frigate",
    }
    outside = {
        "camera": "Porch-1",
        "label": "person",
        "zone": "",
        "event_id": "b",
        "source": "frigate",
    }
    _capture()
    # epoch-scale now so the first fire isn't itself seen as cooled down
    assert f._match_and_fire_event(inside, [rule], {}, now=10000.0, live=True) is not None
    assert f._match_and_fire_event(outside, [rule], {}, now=10001.0, live=True) is None
    print("ok  zone presence rule fires only when the event is inside the zone")


if __name__ == "__main__":
    test_zone_key_and_index()
    test_build_config_includes_zones()
    test_count_in_zone_fires_only_on_its_zone_topic()
    test_whole_frame_rule_ignores_zone_topic()
    test_presence_in_zone()
    print("\nALL ZONE TESTS PASSED")
