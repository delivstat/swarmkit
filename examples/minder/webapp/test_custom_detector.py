"""Standalone tests for the custom-detector deploy loop (Scenario Studio slice 3).

Pins the count/presence threshold + debounce + cooldown + re-arm, and a full _cycle
with an injected dataset module (fake detector) firing through the alert path — no
real model / torch needed. detect_custom itself is validated live with a stock model.

Run in-container:  docker compose exec -T minder python /app/webapp/test_custom_detector.py
"""

import json
import tempfile
from pathlib import Path

import custom_detector as cd


def _fresh():
    cd._state = {}
    cd._cooldown_s = lambda: 600  # type: ignore


def test_presence_default():
    _fresh()
    rule = {"dataset": "d", "camera": "c"}  # default op '>' value 0 -> any detection
    assert cd.evaluate(rule, 0, now=10000.0) is False  # nothing detected
    assert cd.evaluate(rule, 2, now=10000.0) is False  # detected, but debouncing
    print("ok  presence (>0): nothing -> no fire; detected -> starts the debounce")


def test_threshold_debounce_cooldown():
    _fresh()
    rule = {"dataset": "d", "camera": "c", "count_op": ">", "count_value": 3, "debounce_s": 5}
    assert cd.evaluate(rule, 4, now=100.0) is False  # held 0 < 5
    assert cd.evaluate(rule, 4, now=104.0) is False  # held 4 < 5
    assert cd.evaluate(rule, 4, now=105.0) is True  # held 5 -> fire
    assert cd.evaluate(rule, 4, now=106.0) is False  # cooldown
    assert cd.evaluate(rule, 1, now=110.0) is False  # below -> re-arm
    assert cd.evaluate(rule, 4, now=110.0) is False  # debouncing again
    print("ok  threshold + debounce + cooldown + re-arm")


def test_presence_debounce():
    # default debounce_s is 3, so presence needs the condition to hold 3s
    _fresh()
    rule = {"dataset": "d", "camera": "c"}
    assert cd.evaluate(rule, 1, now=1.0) is False
    assert cd.evaluate(rule, 1, now=4.0) is True
    print("ok  presence honours the default debounce window")


class _FakeDS:
    """Minimal stand-in for the dataset module."""

    def __init__(self, count):
        self._count = count

    def has_model(self, name):
        return True

    def _meta(self, name):
        return {"camera": "Porch-1", "class_name": "box"}

    def _grab_snapshot(self, cam):
        return b"x" * 2000

    def detect_custom(self, name, img):
        return {"boxes": [[0, 0, 1, 1]] * self._count, "count": self._count}


def test_cycle_fires_via_alert_path():
    _fresh()
    fired = []
    cd._fire_alert = lambda msg, cam: fired.append((msg, cam))  # type: ignore
    tmp = Path(tempfile.mkdtemp()) / "rules.json"
    tmp.write_text(
        json.dumps(
            [
                {
                    "condition": "boxes on belt",
                    "condition_type": "custom",
                    "dataset": "belt",
                    "camera": "Porch-1",
                    "count_op": ">",
                    "count_value": 2,
                    "debounce_s": 0,
                    "enabled": True,
                }
            ]
        )
    )
    cd.RULES_FILE = tmp  # type: ignore
    # 1 detection -> below threshold (>2) -> no fire
    assert cd._cycle(_ds=_FakeDS(1)) == 0
    cd._state = {}
    # 5 detections -> over threshold -> fires
    assert cd._cycle(_ds=_FakeDS(5)) == 1
    assert fired and "5 detected" in fired[-1][0] and fired[-1][1] == "Porch-1"
    print("ok  _cycle runs the detector + fires a count alert through the alert path")


def test_cycle_noop_without_armed_rules():
    _fresh()
    tmp = Path(tempfile.mkdtemp()) / "rules.json"
    tmp.write_text(json.dumps([{"condition_type": "count", "enabled": True}]))  # not custom
    cd.RULES_FILE = tmp  # type: ignore
    assert cd._cycle(_ds=_FakeDS(9)) == 0  # no inference unless a custom rule is armed
    print("ok  no custom rule armed -> no inference (zero idle load)")


if __name__ == "__main__":
    test_presence_default()
    test_threshold_debounce_cooldown()
    test_presence_debounce()
    test_cycle_fires_via_alert_path()
    test_cycle_noop_without_armed_rules()
    print("\nALL CUSTOM-DETECTOR TESTS PASSED")
