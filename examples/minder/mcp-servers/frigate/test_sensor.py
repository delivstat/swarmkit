"""Standalone tests for sensor-triggered rule execution.

The router can author "stop the pump when the water-level sensor reacts" as a
sensor rule (trigger_entity + state/threshold); the deterministic poller reads
the HA entity each cycle and fires on the RISING edge into the trigger state.
This pins the match logic, the edge-trigger (fire once, not every cycle), the
cooldown, and the device action.

Run in-container:  docker compose exec -T minder python /app/mcp-servers/frigate/test_sensor.py
"""

import server as f


def _stub_states(value):
    f.ha_states = lambda: [{"entity_id": "binary_sensor.leak", "state": value}]  # type: ignore


def _capture():
    alerts, devices = [], []
    f.write_alert = lambda msg, cam="", snap="", vid="": alerts.append(msg)  # type: ignore
    f.execute_device_action = lambda d, a: devices.append((d, a)) or f"{a} {d}"  # type: ignore
    return alerts, devices


RULE = {
    "trigger_entity": "binary_sensor.leak",
    "trigger_sensor": "Water level",
    "trigger_state": "on",
    "actions": [
        {"type": "device", "device": "Water Pump", "action": "turn_off"},
        {"type": "alert"},
    ],
    "enabled": True,
    "target": "minder",
}


def test_matches():
    assert f._sensor_rule_matches({"trigger_state": "on"}, "on") is True
    assert f._sensor_rule_matches({"trigger_state": "on"}, "off") is False
    assert f._sensor_rule_matches({"trigger_state": "open"}, "open") is True
    # numeric threshold
    assert f._sensor_rule_matches({"trigger_op": ">=", "trigger_threshold": 80}, "85") is True
    assert f._sensor_rule_matches({"trigger_op": ">=", "trigger_threshold": 80}, "70") is False
    assert f._sensor_rule_matches({"trigger_op": ">=", "trigger_threshold": 80}, "n/a") is False
    print("ok  _sensor_rule_matches (state + numeric)")


def test_edge_triggered():
    alerts, devices = _capture()
    f.ALERT_COOLDOWN_S = 600
    state = {}
    # sensor off -> no fire, just records not-matched
    _stub_states("off")
    assert f._run_sensor_rules([RULE], state, now=1000.0, live=True) == 0
    # rising edge off->on -> fires once, runs the device action
    _stub_states("on")
    assert f._run_sensor_rules([RULE], state, now=1001.0, live=True) == 1
    assert devices == [("Water Pump", "turn_off")]
    assert alerts and "Water level" in alerts[0]
    # still on -> does NOT re-fire (held, not a new edge)
    assert f._run_sensor_rules([RULE], state, now=1002.0, live=True) == 0
    assert len(devices) == 1
    print("ok  edge-triggered: fires once on rising edge, not while held")


def test_refire_after_reset_and_cooldown():
    _, devices = _capture()
    f.ALERT_COOLDOWN_S = 0  # ignore cooldown for the re-fire check
    state = {}
    _stub_states("on")
    assert f._run_sensor_rules([RULE], state, now=1.0, live=True) == 1  # first edge
    _stub_states("off")
    f._run_sensor_rules([RULE], state, now=2.0, live=True)  # reset
    _stub_states("on")
    assert f._run_sensor_rules([RULE], state, now=3.0, live=True) == 1  # re-armed -> fires again
    assert len(devices) == 2
    print("ok  re-fires after the sensor resets and re-triggers")


def test_cooldown_blocks_rapid_refire():
    _capture()
    f.ALERT_COOLDOWN_S = 600
    state = {}
    _stub_states("on")  # epoch-scale now so the first fire isn't itself cooled down
    assert f._run_sensor_rules([RULE], state, now=10000.0, live=True) == 1
    _stub_states("off")
    f._run_sensor_rules([RULE], state, now=10001.0, live=True)
    _stub_states("on")
    # re-armed 1s later, inside the 600s window -> suppressed
    assert f._run_sensor_rules([RULE], state, now=10002.0, live=True) == 0
    print("ok  cooldown suppresses rapid re-fire")


def test_shadow_mode_no_action():
    _capture()
    recorded = []
    f._record_shadow = recorded.append  # type: ignore
    f.ALERT_COOLDOWN_S = 600
    state = {}
    _stub_states("off")
    f._run_sensor_rules([RULE], state, now=9999.0, live=False)
    _stub_states("on")
    assert f._run_sensor_rules([RULE], state, now=10000.0, live=False) == 1
    assert recorded and recorded[0]["condition"] == "sensor:binary_sensor.leak"
    print("ok  shadow mode logs, runs no device action")


if __name__ == "__main__":
    test_matches()
    test_edge_triggered()
    test_refire_after_reset_and_cooldown()
    test_cooldown_blocks_rapid_refire()
    test_shadow_mode_no_action()
    print("\nALL SENSOR-RULE TESTS PASSED")
