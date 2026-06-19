"""Standalone tests for natural-language authoring of count scenarios.

"alert if more than 3 cars in the driveway" should become a count rule
(condition_type:count + count_object/op/value), parsed deterministically — the 3B
router doesn't emit count fields. Pins the phrase parser (operators, objects, digit +
word numbers, non-count rejection) and that _persist_count_rule writes a valid count
rule (+ dedups).

Run in-container:  docker compose exec -T minder python /app/webapp/test_nl_count.py
"""

import json
import tempfile
from pathlib import Path

import minder_ops as ops


def test_parse_operators_and_objects():
    assert ops._parse_count_scenario("alert if more than 3 cars in the driveway") == {
        "count_object": "car",
        "count_op": ">",
        "count_value": 3,
    }
    assert ops._parse_count_scenario("tell me if at least two people are at the gate") == {
        "count_object": "person",
        "count_op": ">=",
        "count_value": 2,
    }
    assert ops._parse_count_scenario("no more than 7 cars on the lot")["count_op"] == "<="
    assert ops._parse_count_scenario("fewer than 1 dog")["count_op"] == "<"
    assert ops._parse_count_scenario("exactly 2 cats")["count_op"] == "=="
    print("ok  parses operators (> >= <= < ==), objects, and digit/word numbers")


def test_non_count_rejected():
    # no operator phrase, or no countable object, or no number -> not a count scenario
    assert ops._parse_count_scenario("someone is at the door after 10pm") is None
    assert ops._parse_count_scenario("more than the usual traffic") is None  # no object
    assert ops._parse_count_scenario("alert on any car") is None  # no operator/number
    print("ok  non-count phrasing falls through (None)")


def test_persist_count_rule(monkeypatched=None):
    tmp = Path(tempfile.mkdtemp()) / "rules.json"
    ops.RULES_FILE = tmp  # type: ignore
    ops._match_camera_name = lambda c: "Porch-1" if "porch" in (c or "").lower() else "all"  # type: ignore
    ops._load_zone_names = lambda cams: []  # type: ignore
    count = {"count_object": "car", "count_op": ">", "count_value": 3}
    res = ops._persist_count_rule(count, "more than 3 cars on Porch-1", {"cameras": ["Porch-1"]})
    assert res["kind"] == "scenario", res
    rules = json.loads(tmp.read_text())
    assert len(rules) == 1 and rules[0]["condition_type"] == "count", rules
    assert (
        rules[0]["count_object"] == "car"
        and rules[0]["count_op"] == ">"
        and rules[0]["count_value"] == 3
    )
    assert rules[0]["cameras"] == ["Porch-1"]
    # idempotent: the same scenario doesn't duplicate
    ops._persist_count_rule(count, "more than 3 cars on Porch-1", {"cameras": ["Porch-1"]})
    assert len(json.loads(tmp.read_text())) == 1, "duplicate count rule was written"
    print("ok  _persist_count_rule writes a valid count rule and dedups")


def test_persist_with_zone():
    tmp = Path(tempfile.mkdtemp()) / "rules.json"
    ops.RULES_FILE = tmp  # type: ignore
    ops._match_camera_name = lambda c: "Porch-1"  # type: ignore
    ops._load_zone_names = lambda cams: ["driveway"]  # type: ignore
    ops._persist_count_rule(
        {"count_object": "car", "count_op": ">", "count_value": 3},
        "more than 3 cars in the driveway",
        {"cameras": ["Porch-1"]},
    )
    rule = json.loads(tmp.read_text())[0]
    assert rule.get("zone") == "driveway", rule
    print("ok  a zone named in the text is attached to the count rule")


def test_camera_matched_from_text():
    ops._load_cameras = lambda: [{"name": "Porch-1", "ip": "1"}, {"name": "Main-Deck", "ip": "2"}]  # type: ignore
    assert ops._match_camera_in_text("more than 3 cars on Porch-1") == "Porch-1"
    assert ops._match_camera_in_text("alert if over 2 people on the main deck") == "Main-Deck"
    assert ops._match_camera_in_text("more than 3 cars somewhere") == ""
    print("ok  camera named in the text is matched (router fallback)")


def test_persist_scopes_to_named_camera_without_plan():
    tmp = Path(tempfile.mkdtemp()) / "rules.json"
    ops.RULES_FILE = tmp  # type: ignore
    ops._match_camera_name = lambda c: "all"  # type: ignore  (plan gave nothing usable)
    ops._load_cameras = lambda: [{"name": "Porch-1", "ip": "1"}]  # type: ignore
    ops._load_zone_names = lambda cams: []  # type: ignore
    # plan has no cameras -> the camera is recovered from the text
    ops._persist_count_rule(
        {"count_object": "car", "count_op": ">", "count_value": 3},
        "more than 3 cars on Porch-1",
        {},
    )
    assert json.loads(tmp.read_text())[0]["cameras"] == ["Porch-1"]
    print("ok  count rule scopes to the camera named in the text when the router misses it")


if __name__ == "__main__":
    test_parse_operators_and_objects()
    test_non_count_rejected()
    test_persist_count_rule()
    test_persist_with_zone()
    test_camera_matched_from_text()
    test_persist_scopes_to_named_camera_without_plan()
    print("\nALL NL-COUNT TESTS PASSED")
