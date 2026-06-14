"""Standalone test for the /api/rules round-trip.

The matching engine (frigate.server) is the source of truth for the rule shape;
the webapp's MonitoringRule envelope must round-trip it losslessly. Scenario-
authored rules carry `cameras` (list) + `target` and no `camera`, so a narrow
model dropped fields or 422'd — which made deleting/toggling ANY rule fail (the
UI re-saves the whole list). This pins the lossless round-trip.

Run inside the minder container (has pydantic + fastapi):
    docker compose exec -T minder python /app/webapp/test_rules.py
"""

import app

# A realistic mix: template/onboarding rules (camera string) + scenario-authored
# rules (cameras list, target, device action, time rule). Taken from a live box.
RULES = [
    {
        "camera": "all",
        "condition": "person visible after dark",
        "schedule": "night",
        "enabled": True,
        "actions": [],
        "at_time": "",
        "created_ts": 0.0,
    },
    {
        "cameras": ["Porch-1"],
        "condition": "is there a car",
        "schedule": "always",
        "enabled": True,
        "actions": [{"type": "alert"}],
        "at_time": "",
        "created_ts": 1781425747.59,
        "target": "minder",
    },
    {
        "cameras": ["Backyard"],
        "condition": "is there a car",
        "schedule": "always",
        "enabled": True,
        "actions": [
            {"type": "alert"},
            {"type": "device", "device": "Solar Heater Switch 1", "action": "turn_off"},
        ],
        "at_time": "",
        "created_ts": 1781426025.31,
        "target": "minder",
    },
    {
        "cameras": ["all"],
        "condition": "",
        "schedule": "always",
        "enabled": True,
        "actions": [{"type": "alert"}],
        "at_time": "21:00",
        "created_ts": 1781425755.91,
        "target": "minder",
    },
]


def _roundtrip(rules: list[dict]) -> list[dict]:
    """Mirror save_rules: validate the request, then model_dump each rule."""
    req = app.RulesRequest(rules=rules)
    return [r.model_dump() for r in req.rules]


def test_scenario_rule_validates():
    # the camera-less, cameras+target shape must NOT raise (was the 422)
    out = _roundtrip(RULES)
    assert len(out) == len(RULES)
    print("ok  scenario rules validate (no 422)")


def test_lossless_fields():
    out = _roundtrip(RULES)
    car = next(
        r for r in out if r["condition"] == "is there a car" and r.get("cameras") == ["Porch-1"]
    )
    assert car["cameras"] == ["Porch-1"]
    assert car["target"] == "minder"
    assert car["actions"] == [{"type": "alert"}]
    device = next(r for r in out if r.get("cameras") == ["Backyard"])
    assert device["actions"][1]["device"] == "Solar Heater Switch 1"
    assert device["actions"][1]["action"] == "turn_off"
    timed = next(r for r in out if r["at_time"] == "21:00")
    assert timed["cameras"] == ["all"]
    print("ok  lossless: cameras / target / device actions / at_time preserved")


def test_delete_any_rule_persists():
    # delete the scenario rule (index 1) and re-save the rest — must succeed
    remaining = RULES[:1] + RULES[2:]
    out = _roundtrip(remaining)
    assert len(out) == len(RULES) - 1
    assert not any(r.get("cameras") == ["Porch-1"] for r in out)
    # and deleting a template (camera-string) rule also round-trips fine
    out2 = _roundtrip(RULES[1:])
    assert len(out2) == len(RULES) - 1
    print("ok  deleting any rule (scenario or template) persists")


def test_camera_less_sensor_rule():
    # A non-camera rule — "stop the pump when the water-level sensor reacts" — has
    # no camera at all. It must round-trip (camera is not mandatory) and keep its
    # trigger fields intact (extra="allow").
    pump = {
        "condition": "",
        "schedule": "always",
        "enabled": True,
        "trigger_entity": "binary_sensor.water_level_high",
        "trigger_state": "on",
        "actions": [{"type": "device", "device": "Water Pump", "action": "turn_off"}],
        "target": "minder",
    }
    out = _roundtrip([pump])[0]
    assert out["camera"] == "" and out["cameras"] == []  # no camera required
    assert out["trigger_entity"] == "binary_sensor.water_level_high"
    assert out["trigger_state"] == "on"
    assert out["actions"][0] == {"type": "device", "device": "Water Pump", "action": "turn_off"}
    print("ok  camera-less sensor rule (stop pump on water level) round-trips")


def test_camera_match_still_resolves():
    # the round-tripped rules must still resolve to the right cameras: adding an
    # empty `camera`/`cameras` default must not change which cameras a rule watches
    import sys

    sys.path.insert(0, "/app/mcp-servers")
    sys.path.insert(0, "/app/mcp-servers/frigate")
    import server as f

    out = _roundtrip(RULES)
    car = next(r for r in out if r.get("cameras") == ["Porch-1"])
    assert f._rule_cameras(car) == ["Porch-1"]  # cameras list wins over empty camera
    template = next(r for r in out if r["condition"] == "person visible after dark")
    assert f._rule_cameras(template) == ["all"]  # camera string used when cameras empty
    print("ok  _rule_cameras still resolves both shapes")


if __name__ == "__main__":
    test_scenario_rule_validates()
    test_lossless_fields()
    test_delete_any_rule_persists()
    test_camera_less_sensor_rule()
    test_camera_match_still_resolves()
    print("\nALL RULES TESTS PASSED")
