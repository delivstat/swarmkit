"""Standalone tests for Scenario Studio — cross conditions (zone enter / leave).

A line crossing is modelled as a tracked object entering/leaving a zone drawn over
the boundary. This pins: firing on the enter transition (not while merely present),
direction (enter vs leave), the per-object transition tracking across updates, the
per-zone cooldown, and that the presence matcher ignores cross rules.

Run in-container:  docker compose exec -T minder python /app/mcp-servers/frigate/test_cross.py
"""

import server as f


def _capture():
    alerts = []
    f.write_alert = lambda msg, cam="", snap="", vid="": alerts.append((msg, cam))  # type: ignore
    return alerts


def _setup():
    f.ALERT_COOLDOWN_S = 600
    f.POLLER_MODE = "live"
    f._slug_to_name = lambda: {"porch_1": "Porch-1"}  # type: ignore


def _ev(zones, label="car", oid="obj1"):
    return {"id": oid, "label": label, "camera": "porch_1", "current_zones": list(zones)}


ENTER_RULE = {
    "condition_type": "cross",
    "object": "car",
    "zone": "lane",
    "direction": "enter",
    "cameras": ["Porch-1"],
    "enabled": True,
    "actions": [{"type": "alert"}],
}
LEAVE_RULE = {**ENTER_RULE, "direction": "leave"}


def test_fires_on_enter_not_on_presence():
    _setup()
    _capture()
    st = {}
    # object outside the zone -> nothing
    assert f.handle_cross_event(_ev([]), [ENTER_RULE], st, now=10000.0, live=True) is None
    # object enters the lane -> fires
    assert (
        f.handle_cross_event(_ev(["porch_1__lane"]), [ENTER_RULE], st, now=10001.0, live=True)
        is not None
    )
    # still in the lane (presence, not a new crossing) -> does NOT re-fire
    assert (
        f.handle_cross_event(_ev(["porch_1__lane"]), [ENTER_RULE], st, now=10002.0, live=True)
        is None
    )
    print("ok  enter rule fires on the crossing-in transition, not while present")


def test_leave_direction():
    _setup()
    _capture()
    st = {}
    f.handle_cross_event(_ev(["porch_1__lane"]), [LEAVE_RULE], st, now=20000.0, live=True)  # enter
    # leaves the lane -> leave rule fires
    assert f.handle_cross_event(_ev([]), [LEAVE_RULE], st, now=20001.0, live=True) is not None
    # an ENTER rule would NOT have fired on that leave
    st2 = {}
    f.handle_cross_event(_ev(["porch_1__lane"]), [ENTER_RULE], st2, now=20000.0, live=True)
    assert f.handle_cross_event(_ev([]), [ENTER_RULE], st2, now=20001.0, live=True) is None
    print("ok  leave fires on exit; enter does not fire on exit")


def test_object_and_camera_filter():
    _setup()
    _capture()
    st = {}
    # a person entering does not match a car cross rule
    assert (
        f.handle_cross_event(
            _ev(["porch_1__lane"], label="person"), [ENTER_RULE], st, now=30000.0, live=True
        )
        is None
    )
    print("ok  cross respects the object filter")


def test_cooldown():
    _setup()
    _capture()
    st = {}
    assert (
        f.handle_cross_event(
            _ev(["porch_1__lane"], oid="a"), [ENTER_RULE], st, now=40000.0, live=True
        )
        is not None
    )
    # a second object crossing within the cooldown window is suppressed (anti-spam)
    assert (
        f.handle_cross_event(
            _ev(["porch_1__lane"], oid="b"), [ENTER_RULE], st, now=40005.0, live=True
        )
        is None
    )
    print("ok  per-zone cooldown suppresses rapid repeat crossings")


def test_presence_matcher_skips_cross():
    rule = {
        "condition_type": "cross",
        "zone": "lane",
        "object": "car",
        "condition": "car in lane",
        "cameras": ["Porch-1"],
    }
    ev = {
        "camera": "Porch-1",
        "label": "car",
        "zone": "porch_1__lane",
        "event_id": "x",
        "source": "frigate",
    }
    assert f._match_and_fire_event(ev, [rule], {}, now=1.0, live=True) is None
    print("ok  presence matcher skips cross rules (no double-fire)")


if __name__ == "__main__":
    test_fires_on_enter_not_on_presence()
    test_leave_direction()
    test_object_and_camera_filter()
    test_cooldown()
    test_presence_matcher_skips_cross()
    print("\nALL CROSS TESTS PASSED")
