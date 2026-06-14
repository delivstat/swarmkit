"""Standalone tests for the media-when-ready alert path.

A live MQTT "new" event fires before Frigate has written the snapshot, and the
clip only exists once the event ends — so the text alert must go out instantly
and the snapshot/clip follow as soon as they exist. These tests pin that:
_await_media's poll/timeout, the boxed-snapshot + description + clip follow-ups,
and _fire dispatching to the right media path per source.

Run inside the minder container (has mcp + yaml):
    docker compose exec minder python /app/mcp-servers/frigate/test_media.py
"""

import json

import server as f


class _SyncThread:
    """Drop-in for threading.Thread that runs target synchronously, so the
    daemon-thread media delivery is deterministic in tests."""

    def __init__(self, target=None, daemon=False):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _capture_alerts(monkeypatch_target):
    """Replace write_alert with a recorder; returns the list it appends to."""
    calls: list[tuple] = []
    monkeypatch_target.write_alert = lambda msg, cam="", snap="", vid="": calls.append(
        (msg, cam, snap, vid)
    )
    return calls


def test_describe_prompt_grounded():
    # the prompt anchors to the detected object and forbids the fabrication moves
    # that produced "person in a red shirt" / "plate 960142" on a car detection
    car = f._describe_prompt("car")
    assert "vehicle" in car and "Describe only that vehicle" in car
    for ban in ("licence plate", "not clearly visible", "do NOT guess how many", "ONE short"):
        assert ban in car, ban
    # person/animal map through; unknown/empty label → generic, still constrained
    assert "person" in f._describe_prompt("person")
    assert "animal" in f._describe_prompt("dog")
    generic = f._describe_prompt("")
    assert "clearly visible" in generic and "licence plate" in generic
    print("ok  _describe_prompt grounded + fabrication-forbidden")


def test_describe_prompt_alert_cause():
    # the alert's cause (camera + watch condition) is fed to the model, with a
    # guard against simply restating the condition
    p = f._describe_prompt("car", cam="Main Gate", condition="is there a car")
    assert "on the Main Gate camera" in p
    assert 'watching for: "is there a car"' in p
    assert "do not simply restate the watch condition" in p
    # no cause given → no camera/watch clauses leak in
    bare = f._describe_prompt("car")
    assert "camera detected a vehicle" in bare and "watching for" not in bare
    print("ok  _describe_prompt feeds alert cause (camera + condition)")


def test_await_media_ready():
    # path present on the first fetch → returned immediately
    assert f._await_media(lambda: json.dumps({"path": "/data/x.jpg"}), 5) == "/data/x.jpg"
    print("ok  _await_media ready")


def test_await_media_timeout():
    # never ready, zero timeout → "" (graceful), exactly one fetch attempt
    n = {"calls": 0}

    def _fetch():
        n["calls"] += 1
        return json.dumps({"path": ""})

    assert f._await_media(_fetch, 0) == ""
    assert n["calls"] == 1, n
    print("ok  _await_media timeout")


def test_deliver_event_media_full():
    f.threading.Thread = _SyncThread  # type: ignore
    f.DESCRIBE_ENABLED = True
    f.RECORD_ENABLED = True
    f.get_event_snapshot = lambda eid: json.dumps({"path": "/data/evt.jpg"})  # type: ignore
    f.get_event_clip = lambda eid: json.dumps({"path": "/data/evt.mp4"})  # type: ignore
    f._describe_snapshot = lambda p, label="", cam="", condition="": (  # type: ignore
        "a person in a red jacket at the door"
    )
    calls = _capture_alerts(f)

    f._deliver_event_media("evt1", "Front Door")

    # photo follow-up (no text bubble), then the VLM description, then the clip
    assert calls[0] == ("", "Front Door", "/data/evt.jpg", "")
    assert calls[1][0] == "📷 Front Door: a person in a red jacket at the door"
    assert calls[2] == ("", "Front Door", "", "/data/evt.mp4")
    assert len(calls) == 3, calls
    print("ok  _deliver_event_media full (photo + description + clip)")


def test_deliver_event_media_no_snapshot():
    # snapshot never materialises → no photo, no description; recording off too
    f.threading.Thread = _SyncThread  # type: ignore
    f.DESCRIBE_ENABLED = True
    f.RECORD_ENABLED = False
    f.get_event_snapshot = lambda eid: json.dumps({"path": ""})  # type: ignore
    f.SNAPSHOT_WAIT_S = 0
    calls = _capture_alerts(f)

    f._deliver_event_media("evt2", "Gate")

    assert calls == [], calls
    f.SNAPSHOT_WAIT_S = 20
    print("ok  _deliver_event_media no snapshot (graceful)")


def test_deliver_event_media_describe_off():
    f.threading.Thread = _SyncThread  # type: ignore
    f.DESCRIBE_ENABLED = False
    f.RECORD_ENABLED = True
    f.get_event_snapshot = lambda eid: json.dumps({"path": "/data/e.jpg"})  # type: ignore
    f.get_event_clip = lambda eid: json.dumps({"path": "/data/e.mp4"})  # type: ignore
    calls = _capture_alerts(f)

    f._deliver_event_media("evt3", "Porch")

    # photo + clip, no description message
    assert calls == [("", "Porch", "/data/e.jpg", ""), ("", "Porch", "", "/data/e.mp4")], calls
    f.DESCRIBE_ENABLED = True
    print("ok  _deliver_event_media describe off")


def test_fire_frigate_instant_text_then_media():
    f.threading.Thread = _SyncThread  # type: ignore
    dispatched: list[tuple] = []
    f._deliver_event_media = lambda eid, cam, label="", condition="": dispatched.append(
        ("frigate", eid, cam, label)
    )  # type: ignore
    f._deliver_ha_media = lambda ev, cam, condition="": dispatched.append(("ha", cam))  # type: ignore
    calls = _capture_alerts(f)

    ev = {"source": "frigate", "event_id": "e9", "camera": "Front Door", "label": "person"}
    rule = {"condition": "is there a person", "actions": [{"type": "alert"}]}
    mode = f._fire(rule, ev, now=1000.0, live=True)

    assert mode == "live"
    # instant text alert, no media attached inline
    assert calls == [("is there a person — Front Door", "Front Door", "", "")], calls
    # media delivery dispatched to the frigate path only, with YOLO's label
    assert dispatched == [("frigate", "e9", "Front Door", "person")], dispatched
    print("ok  _fire frigate: instant text + frigate media dispatch")


def test_fire_ha_dispatch():
    f.threading.Thread = _SyncThread  # type: ignore
    dispatched: list[tuple] = []
    f._deliver_event_media = lambda eid, cam, label="", condition="": dispatched.append(
        ("frigate", eid, cam, label)
    )  # type: ignore
    f._deliver_ha_media = lambda ev, cam, condition="": dispatched.append(("ha", cam))  # type: ignore
    calls = _capture_alerts(f)

    ev = {"source": "ha", "event_id": "ha:cam.x", "camera": "Living Room", "label": "person"}
    rule = {"condition": "someone in the living room", "actions": [{"type": "alert"}]}
    f._fire(rule, ev, now=1000.0, live=True)

    assert len(calls) == 1 and calls[0][1] == "Living Room"
    assert dispatched == [("ha", "Living Room")], dispatched
    print("ok  _fire ha: ha media dispatch")


def test_fire_shadow_no_alert():
    captured: list[dict] = []
    f._record_shadow = captured.append  # type: ignore
    calls = _capture_alerts(f)
    f._deliver_event_media = lambda eid, cam, label="", condition="": calls.append(("MEDIA",))  # type: ignore

    ev = {"source": "frigate", "event_id": "e", "camera": "Gate", "label": "person"}
    rule = {"condition": "person at gate", "actions": [{"type": "alert"}]}
    mode = f._fire(rule, ev, now=1000.0, live=False)

    assert mode == "shadow"
    assert calls == [], calls  # no real alert, no media in shadow
    assert captured and captured[0]["would_alert"] == "person at gate — Gate"
    print("ok  _fire shadow: logs only, no alert/media")


def test_fire_device_action_note():
    f.threading.Thread = _SyncThread  # type: ignore
    f._deliver_event_media = lambda eid, cam, label="", condition="": None  # type: ignore
    f.execute_device_action = lambda device, action: f"turned {action} {device}"  # type: ignore
    calls = _capture_alerts(f)

    ev = {"source": "frigate", "event_id": "e", "camera": "Gate", "label": "person"}
    rule = {
        "condition": "person at gate",
        "actions": [{"type": "device", "device": "porch light", "action": "turn_on"}],
    }
    f._fire(rule, ev, now=1000.0, live=True)

    # device-only rule still alerts (note appended to the instant text)
    assert "turned turn_on porch light" in calls[0][0]
    print("ok  _fire device action note")


if __name__ == "__main__":
    test_describe_prompt_grounded()
    test_describe_prompt_alert_cause()
    test_await_media_ready()
    test_await_media_timeout()
    test_deliver_event_media_full()
    test_deliver_event_media_no_snapshot()
    test_deliver_event_media_describe_off()
    test_fire_frigate_instant_text_then_media()
    test_fire_ha_dispatch()
    test_fire_shadow_no_alert()
    test_fire_device_action_note()
    print("\nALL MEDIA TESTS PASSED")
