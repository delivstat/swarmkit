"""Standalone test for the deterministic device-control endpoint.

Device control is code, not LLM: /api/devices/control calls an exact HA service
on an exact entity, checked against the per-domain allowlist. This pins that the
allowlist accepts valid calls (and passes their data through) and rejects unknown
domains / services / parameters — so the passthrough can never call an arbitrary
HA service.

Run in-container:  docker compose exec -T minder python /app/webapp/test_devices.py
"""

import asyncio

import app
from fastapi import HTTPException


def _fake_ha(recorder):
    """Fake _ha_api: record service calls; return a plausible entity on state read."""

    def _ha_api(endpoint, token, method="GET", data=None):
        if endpoint.startswith("services/"):
            recorder.append((endpoint, data))
            return {}
        if endpoint.startswith("states/"):
            eid = endpoint.split("/", 1)[1]
            return {"entity_id": eid, "state": "on", "attributes": {"friendly_name": "Test"}}
        return {}

    return _ha_api


def _call(entity_id, service, data=None):
    req = app.DeviceControlRequest(entity_id=entity_id, service=service, data=data or {})
    return asyncio.run(app.control_device_endpoint(req))


def _setup(recorder):
    app._get_ha_token = lambda: "tok"  # type: ignore
    app._ha_api = _fake_ha(recorder)  # type: ignore


def test_switch_turn_on():
    calls = []
    _setup(calls)
    res = _call("switch.solar_heater_1", "turn_on")
    assert res["status"] == "ok"
    assert calls == [("services/switch/turn_on", {"entity_id": "switch.solar_heater_1"})]
    assert res["device"]["id"] == "switch.solar_heater_1"
    print("ok  switch turn_on -> exact HA service call + fresh state")


def test_light_brightness_passes_through():
    calls = []
    _setup(calls)
    _call("light.porch", "turn_on", {"brightness_pct": 50})
    assert calls == [("services/light/turn_on", {"entity_id": "light.porch", "brightness_pct": 50})]
    print("ok  light brightness_pct passed through")


def test_lock_unlock():
    calls = []
    _setup(calls)
    _call("lock.front_door", "unlock")
    assert calls[0][0] == "services/lock/unlock"
    print("ok  lock unlock maps to lock domain service")


def _expect_400(entity_id, service, data=None):
    try:
        _call(entity_id, service, data)
    except HTTPException as e:
        assert e.status_code == 400, e.status_code
        return
    raise AssertionError(f"expected 400 for {entity_id}/{service} {data}")


def test_rejects_unknown_domain():
    _setup([])
    _expect_400("sensor.temperature", "turn_on")
    print("ok  rejects non-controllable domain")


def test_rejects_disallowed_service():
    _setup([])
    _expect_400("switch.x", "open_cover")  # cover service on a switch
    print("ok  rejects service not allowed for the domain")


def test_rejects_unexpected_param():
    _setup([])
    _expect_400("switch.x", "turn_on", {"brightness_pct": 50})  # switch can't take brightness
    _expect_400("light.x", "turn_on", {"evil": 1})
    print("ok  rejects unexpected parameters")


def test_dedupe_drops_ghost_duplicates():
    # the live pressure-pump (_2, "on") + its abandoned ghost (same name,
    # unavailable) -> keep only the live one; a lone unavailable device is kept.
    devs = [
        {"id": "switch.pump", "name": "Pump", "type": "switch", "state": "unavailable"},
        {"id": "switch.pump_2", "name": "Pump", "type": "switch", "state": "on"},
        {"id": "switch.heater", "name": "Heater", "type": "switch", "state": "unavailable"},
        {"id": "light.porch", "name": "Porch", "type": "light", "state": "off"},
    ]
    out = app._dedupe_devices(devs)
    names = sorted(d["name"] for d in out)
    assert names == ["Heater", "Porch", "Pump"], names  # one Pump, ghost dropped
    pump = next(d for d in out if d["name"] == "Pump")
    assert pump["id"] == "switch.pump_2" and pump["state"] == "on"  # the live one
    heater = next(d for d in out if d["name"] == "Heater")
    assert heater["state"] == "unavailable"  # lone offline device still shown
    print("ok  dedupe drops ghost duplicates, keeps live + lone-offline")


if __name__ == "__main__":
    test_switch_turn_on()
    test_light_brightness_passes_through()
    test_lock_unlock()
    test_rejects_unknown_domain()
    test_rejects_disallowed_service()
    test_rejects_unexpected_param()
    test_dedupe_drops_ghost_duplicates()
    print("\nALL DEVICE-CONTROL TESTS PASSED")
