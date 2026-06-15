"""Standalone test for sensor-rule authoring (router plan -> persisted rule).

"stop the pump when the water level is high" -> the router emits a sensor plan;
_persist_sensor_rule resolves the HA sensor entity, picks a state/threshold, and
writes a sensor rule the poller can watch. Pins entity resolution, binary vs
numeric thresholds, the device action, and the no-match message.

Run in-container:  docker compose exec -T minder python /app/webapp/test_sensor_authoring.py
"""

import json
import tempfile
from pathlib import Path

import minder_ops as ops

SENSORS = [
    {"id": "binary_sensor.water_level_high", "name": "Water level high", "state": "off"},
    {"id": "sensor.tank_level", "name": "Tank level", "state": "42"},
    {"id": "binary_sensor.front_door", "name": "Front door", "state": "off"},
]


def _setup(tmp):
    ops._ha_sensors = lambda: SENSORS  # type: ignore
    ops._match_device_name = lambda d: "Water Pump" if "pump" in d.lower() else None  # type: ignore
    ops._device_grounded_in_request = lambda d, r: True  # type: ignore
    ops.RULES_FILE = Path(tmp) / "rules.json"  # type: ignore


def _rules(tmp):
    f = Path(tmp) / "rules.json"
    return json.loads(f.read_text()) if f.exists() else []


def test_binary_sensor_rule():
    tmp = tempfile.mkdtemp()
    _setup(tmp)
    res = ops._persist_sensor_rule(
        {
            "trigger_sensor": "water level",
            "trigger_value": "high",
            "device": "pump",
            "devices": ["pump"],
            "device_action": "turn_off",
            "alert": True,
            "schedule": "always",
        },
        "stop the pump when the water level is high",
    )
    assert res["kind"] == "scenario", res
    rule = _rules(tmp)[0]
    assert rule["trigger_entity"] == "binary_sensor.water_level_high"  # resolved
    assert rule["trigger_state"] == "on"  # "high" -> active
    assert {"type": "device", "device": "Water Pump", "action": "turn_off"} in rule["actions"]
    assert any(a["type"] == "alert" for a in rule["actions"])
    print("ok  binary sensor rule authored (entity resolved, pump action)")


def test_numeric_threshold_rule():
    tmp = tempfile.mkdtemp()
    _setup(tmp)
    ops._persist_sensor_rule(
        {
            "trigger_sensor": "tank level",
            "trigger_value": "80",
            "alert": True,
            "schedule": "always",
        },
        "alert me when the tank level reaches 80",
    )
    rule = _rules(tmp)[0]
    assert rule["trigger_entity"] == "sensor.tank_level"
    assert rule["trigger_op"] == ">=" and rule["trigger_threshold"] == 80.0
    print("ok  numeric threshold rule (>= 80)")


def test_no_sensor_match():
    tmp = tempfile.mkdtemp()
    _setup(tmp)
    res = ops._persist_sensor_rule(
        {"trigger_sensor": "lava temperature", "trigger_value": "high", "alert": True},
        "alert me when the lava temperature is high",
    )
    assert "couldn't find a sensor" in res["text"].lower()
    assert _rules(tmp) == []  # nothing written
    print("ok  no sensor match -> helpful message, no rule written")


if __name__ == "__main__":
    test_binary_sensor_rule()
    test_numeric_threshold_rule()
    test_no_sensor_match()
    print("\nALL SENSOR-AUTHORING TESTS PASSED")
