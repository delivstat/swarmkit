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
    for k in ["snapshot", "video", "weather", "chat", "device_now"]:
        assert _kind(k, "hi") == k, k
    print("ok  non-list/non-scenario kinds untouched")


def _subject(text, given=None):
    plan = {"kind": "query"}
    if given is not None:
        plan["subject"] = given
    ops._correct_plan(plan, text)
    return plan.get("subject") or ""


def test_subject_inferred_for_object_queries():
    # The 3B router often returns subject=null even for an obvious object query;
    # this is exactly what sent "is there a car in the porch" to the slow VLM.
    assert _subject("is there a car in the porch") == "vehicle"
    assert _subject("anyone at the gate?") == "person"
    assert _subject("is there a dog in the yard") == "animal"
    assert _subject("any vehicles outside") == "vehicle"
    print("ok  null-subject object queries inferred -> person/vehicle/animal (fast YOLO)")


def test_subject_inference_does_not_override_router():
    # If the router already named a subject, keep it.
    assert _subject("is there a car", given="person") == "person"
    print("ok  router-supplied subject preserved")


def test_open_scene_query_stays_open():
    # No object noun -> subject stays empty -> open_scene VLM path (intended).
    assert _subject("describe the scene") == ""
    assert _subject("what's going on outside") == ""
    print("ok  open-scene questions keep empty subject (VLM path)")


def test_scenario_tagged_question_gets_subject_and_camera():
    # The exact failing shape: the 3B tags "is there a car in the porch" as a
    # scenario, emitting trigger_object/trigger_camera and a null subject. The
    # guard must demote to query AND fill subject (-> fast YOLO) + cameras.
    plan = {
        "kind": "scenario",
        "trigger_object": "car",
        "trigger_camera": "Porch-1",
        "subject": None,
        "cameras": None,
    }
    ops._correct_plan(plan, "is there a car in the porch")
    assert plan["kind"] == "query", plan
    assert plan["subject"] == "vehicle", plan
    assert plan["cameras"] == ["Porch-1"], plan
    print("ok  scenario-tagged object question -> query + vehicle + camera (fast YOLO)")


if __name__ == "__main__":
    test_smalltalk_demoted_to_chat()
    test_real_list_requests_preserved()
    test_scenario_guard_still_works()
    test_other_kinds_untouched()
    test_subject_inferred_for_object_queries()
    test_subject_inference_does_not_override_router()
    test_open_scene_query_stays_open()
    test_scenario_tagged_question_gets_subject_and_camera()
    print("\nALL CORRECT-PLAN TESTS PASSED")
