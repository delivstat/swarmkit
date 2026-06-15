"""Standalone test for _correct_plan — the deterministic guard on router output.

The 3B router wobbles: it labels bare greetings ("hi", "thanks") as camera_list,
and plain questions as scenario. _correct_plan fixes both structurally (no LLM),
without breaking genuine list/scenario requests. Pure function — fast.

Run in-container:  docker compose exec -T minder python /app/webapp/test_correct_plan.py
"""

import minder_ops as ops


def _kind(kind, text):
    plan = {"kind": kind}
    ops._correct_plan(plan, text)
    return plan["kind"]


def test_smalltalk_demoted_to_chat():
    for greeting in ["hi", "hey there", "thanks", "yo", "good morning", "thank you"]:
        assert _kind("camera_list", greeting) == "chat", greeting
        assert _kind("device_list", greeting) == "chat", greeting
    print("ok  greetings mislabelled camera_list/device_list -> chat")


def test_real_list_requests_preserved():
    assert _kind("camera_list", "what cameras do you have") == "camera_list"
    assert _kind("camera_list", "list my cameras") == "camera_list"
    assert _kind("camera_list", "show me the cameras") == "camera_list"
    assert _kind("device_list", "list devices") == "device_list"
    assert _kind("device_list", "what can you control") == "device_list"
    assert _kind("device_list", "what lights do I have") == "device_list"
    print("ok  genuine camera_list/device_list requests preserved")


def test_scenario_guard_still_works():
    # a plain question wrongly tagged scenario -> query; a real trigger stays
    assert _kind("scenario", "is there a car in the driveway?") == "query"
    assert _kind("scenario", "turn on the light when you see a car") == "scenario"
    assert _kind("scenario", "every night at 9pm turn on the porch light") == "scenario"
    print("ok  scenario guard intact (question -> query, trigger -> scenario)")


def test_other_kinds_untouched():
    for k in ["query", "snapshot", "video", "weather", "chat", "device_now"]:
        assert _kind(k, "hi") == k, k
    print("ok  non-list/non-scenario kinds untouched")


if __name__ == "__main__":
    test_smalltalk_demoted_to_chat()
    test_real_list_requests_preserved()
    test_scenario_guard_still_works()
    test_other_kinds_untouched()
    print("\nALL CORRECT-PLAN TESTS PASSED")
