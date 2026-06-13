"""Standalone tests for the Frigate config generator + event normalizer.

Run inside the minder container (has mcp + yaml):
    docker compose exec minder python /app/mcp-servers/frigate/test_config.py
"""

import yaml

import server as f


def test_slug():
    assert f._slug("Main-Door") == "main_door"
    assert f._slug("Main Gate - 2") == "main_gate_2"
    assert f._slug("Pathway-Gate-2") == "pathway_gate_2"
    assert f._slug("Porch-1") == "porch_1"
    print("ok  _slug")


def test_build_config():
    cams = [
        {"ip": "192.168.0.101", "name": "Porch-1", "rtsp_url": "rtsp://x", "onvif": True},
        {"ip": "192.168.0.103", "name": "Main Gate - 2", "rtsp_url": "rtsp://y", "onvif": True},
    ]
    cfg = f._build_config(cams)
    # round-trips as valid YAML
    cfg = yaml.safe_load(yaml.safe_dump(cfg))
    assert set(cfg["cameras"]) == {"porch_1", "main_gate_2"}
    assert set(cfg["go2rtc"]["streams"]) == {"porch_1", "main_gate_2"}
    assert cfg["detectors"]["cpu1"]["type"] == "cpu"
    # sub-stream + templated password, no plaintext secret
    url = cfg["go2rtc"]["streams"]["porch_1"][0]
    assert "subtype=1" in url and "{FRIGATE_RTSP_PASSWORD}" in url
    cam = cfg["cameras"]["porch_1"]
    assert cam["ffmpeg"]["inputs"][0]["path"] == "rtsp://127.0.0.1:8554/porch_1"
    assert cam["detect"]["enabled"] is True
    assert cam["objects"]["track"] == ["person", "car", "dog", "cat"]
    print("ok  _build_config")


def test_frigate_cameras_filter(monkeypatch_file=None):
    # tier-explicit wins; untiered falls back to rtsp+onvif; ha-snapshot excluded
    cams = [
        {"ip": "1", "name": "a", "tier": "frigate", "rtsp_url": "r", "onvif": True},
        {"ip": "2", "name": "b", "rtsp_url": "r", "onvif": True},        # fallback include
        {"ip": "3", "name": "c", "tier": "ha-snapshot", "rtsp_url": "", "onvif": False},
        {"ip": "4", "name": "d", "rtsp_url": "", "onvif": False},        # no stream → excluded
    ]
    f._load_cameras = lambda: cams  # type: ignore
    keys = {c["ip"] for c in f._frigate_cameras()}
    assert keys == {"1", "2"}, keys
    print("ok  _frigate_cameras filter")


def test_normalize():
    ev = {
        "id": "evt1", "camera": "main_gate_2", "label": "person",
        "zones": ["driveway"], "start_time": 100.0, "top_score": 0.9,
        "has_snapshot": True, "has_clip": False, "data": {"description": None},
    }
    n = f._normalize(ev, {"main_gate_2": "Main Gate - 2"})
    assert n["source"] == "frigate"
    assert n["camera"] == "Main Gate - 2"        # slug → friendly name
    assert n["label"] == "person"
    assert n["zone"] == "driveway"
    assert n["snapshot_ref"] == "frigate:evt1"
    assert n["clip_ref"] == ""                    # has_clip False
    assert n["description"] is None
    print("ok  _normalize")


def test_genai_config():
    cams = [{"ip": "192.168.0.103", "name": "Main Gate - 2",
             "rtsp_url": "rtsp://y", "onvif": True}]
    f.GENAI_ENABLED = True
    cfg = yaml.safe_load(yaml.safe_dump(f._build_config(cams)))
    assert cfg["genai"]["provider"] == "ollama"
    assert cfg["genai"]["model"] == f.VISION_MODEL
    assert "person" in cfg["objects"]["genai"]["object_prompts"]
    cam_genai = cfg["cameras"]["main_gate_2"]["objects"]["genai"]
    assert cam_genai["enabled"] is True
    assert cam_genai["use_snapshot"] is True
    assert cam_genai["objects"] == ["person", "car"]
    # disabled → no genai anywhere
    f.GENAI_ENABLED = False
    cfg2 = f._build_config(cams)
    assert "genai" not in cfg2
    assert "genai" not in cfg2["cameras"]["main_gate_2"]["objects"]
    f.GENAI_ENABLED = True
    print("ok  _build_config genai")


def test_condition_to_labels():
    assert f._condition_to_labels("is there a person") == {"person"}
    assert f._condition_to_labels("someone at the gate") == {"person"}
    assert f._condition_to_labels("a car in the driveway") == {"car"}
    assert f._condition_to_labels("any animal") == {"dog", "cat"}
    assert f._condition_to_labels("is the gate open") == set()  # non-object
    print("ok  _condition_to_labels")


def test_camera_match():
    # single-element list (and "all")
    assert f._camera_match(["all"], "Main Gate - 2") is True
    assert f._camera_match([""], "Porch-1") is True
    assert f._camera_match(["Main Gate"], "Main Gate - 2") is True
    assert f._camera_match(["Porch"], "Backyard") is False
    # multi-camera: matches if ANY listed camera matches
    assert f._camera_match(["Front Door", "Main Gate"], "Main Gate - 2") is True
    assert f._camera_match(["Front Door", "Backyard"], "Main Gate - 2") is False
    # backward-compat: old single-camera rule
    assert f._rule_cameras({"camera": "Porch-1"}) == ["Porch-1"]
    assert f._rule_cameras({"cameras": ["A", "B"]}) == ["A", "B"]
    print("ok  _camera_match (multi)")


def test_ha_events():
    # HA-tier camera whose motion sensor is "on" since the cursor → person event
    import time
    cams = [{"ip": "", "name": "Living Room", "tier": "ha-snapshot",
             "ha_entity": "binary_sensor.living_room_motion",
             "snapshot_url": "camera.living_room", "onvif": False}]
    f._load_cameras = lambda: cams  # type: ignore
    now = time.time()
    f.ha_states = lambda token="": [  # type: ignore
        {"entity_id": "binary_sensor.living_room_motion", "state": "on",
         "last_changed": __import__("datetime").datetime.fromtimestamp(
             now, __import__("datetime").timezone.utc).isoformat()},
    ]
    evs = f._fetch_ha_events(after=now - 60)
    assert len(evs) == 1, evs
    e = evs[0]
    assert e["source"] == "ha" and e["label"] == "person"
    assert e["camera"] == "Living Room"
    assert e["snapshot_ref"] == "ha:camera.living_room"
    # already-seen (before cursor) → no event
    assert f._fetch_ha_events(after=now + 10) == []
    # sensor off → no event
    f.ha_states = lambda token="": [  # type: ignore
        {"entity_id": "binary_sensor.living_room_motion", "state": "off",
         "last_changed": "2026-06-13T00:00:00+00:00"}]
    assert f._fetch_ha_events(after=0) == []
    print("ok  _fetch_ha_events")


if __name__ == "__main__":
    test_slug()
    test_build_config()
    test_frigate_cameras_filter()
    test_normalize()
    test_genai_config()
    test_condition_to_labels()
    test_camera_match()
    test_ha_events()
    print("\nALL FRIGATE TESTS PASSED")
