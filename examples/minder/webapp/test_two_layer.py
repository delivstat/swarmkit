"""Standalone tests for the two vision model layers (design/two-layer-vision-models.md).

Pins the describe-intent router guard (Layer 2 description vs Layer 1 YOLO presence)
and the Layer 2 cloud VLM branch (parses an OpenAI-compatible multimodal reply, and
degrades to the local VLM without a key / on error).

Run in-container:  docker compose exec -T minder python /app/webapp/test_two_layer.py
"""

import io
import json

import minder_ops as ops


class _Resp(io.BytesIO):
    """Minimal urlopen() return — a context-free object with .read()."""


def _fake_urlopen(body: dict):
    def _open(req, timeout=0):
        return _Resp(json.dumps(body).encode())

    return _open


def test_describe_intent_vs_presence():
    # Activity / judgment phrasings → Layer 2 (read the scene).
    for q in [
        "what is the person doing at the office",
        "what's happening at the gate",
        "is there any danger at the porch",
        "describe the driveway",
        "what is the man carrying",
    ]:
        assert ops._wants_description(q), q
    # Pure presence questions → Layer 1 (fast YOLO yes/no).
    for q in ["is anyone at the office", "is there a car in the driveway", "any people outside"]:
        assert not ops._wants_description(q), q
    print("ok  describe-intent splits Layer 2 (describe) from Layer 1 (presence)")


def test_layer2_cloud_parses_multimodal_reply():
    ops.OPENROUTER_KEY = "test-key"
    orig = ops.urllib.request.urlopen
    ops.urllib.request.urlopen = _fake_urlopen(
        {"choices": [{"message": {"content": "A person in a red jacket is waving at the camera."}}]}
    )
    try:
        out = ops._vlm_answer_cloud("aGVsbG8=", "what is happening")
    finally:
        ops.urllib.request.urlopen = orig
    assert out == "A person in a red jacket is waving at the camera.", out
    print("ok  Layer 2 cloud parses the multimodal OpenAI-compatible reply")


def test_layer2_cloud_no_key_is_empty():
    orig_key = ops.OPENROUTER_KEY
    ops.OPENROUTER_KEY = ""
    try:
        assert ops._vlm_answer_cloud("aGVsbG8=", "q") == ""
    finally:
        ops.OPENROUTER_KEY = orig_key
    print("ok  Layer 2 cloud with no key is a graceful empty string (falls back to local)")


def test_vlm_answer_falls_back_to_local_on_cloud_miss():
    ops.QUERY_PROVIDER = "openrouter"
    ops.OPENROUTER_KEY = "test-key"
    orig_cloud = ops._vlm_answer_cloud
    orig_open = ops.urllib.request.urlopen
    ops.urllib.request.urlopen = _fake_urlopen({"message": {"content": "LOCAL-VLM"}})
    try:
        ops._vlm_answer_cloud = lambda frame, prompt: ""  # cloud miss
        assert ops._vlm_answer("aGVsbG8=", "what is happening", "office") == "LOCAL-VLM"
    finally:
        ops._vlm_answer_cloud = orig_cloud
        ops.urllib.request.urlopen = orig_open
    print("ok  _vlm_answer falls back to the local VLM when Layer 2 cloud misses")


def test_camera_match_prefers_full_coverage_over_partial():
    # "in front of the office": "office" fully matches the Office cam; "front" only
    # partially matches Front-Right. Office must win — the preposition "front" must
    # not hijack a describe query onto the Front-Right camera (the porch-bug class).
    orig = ops._load_cameras
    ops._load_cameras = lambda: [
        {"name": "Porch-1", "ip": "192.168.0.101"},
        {"name": "Front-Right", "ip": "192.168.0.106"},
        {"name": "Office", "ip": "192.168.0.109"},
    ]
    try:
        assert ops._match_camera_name("what is the person doing in front of the office") == "Office"
        assert ops._match_camera_name("front right camera") == "Front-Right"
        assert ops._match_camera_name("anything unmatched") == "all"
    finally:
        ops._load_cameras = orig
    print("ok  camera match prefers full coverage (office) over partial (front)")


if __name__ == "__main__":
    test_describe_intent_vs_presence()
    test_layer2_cloud_parses_multimodal_reply()
    test_layer2_cloud_no_key_is_empty()
    test_vlm_answer_falls_back_to_local_on_cloud_miss()
    test_camera_match_prefers_full_coverage_over_partial()
    print("\nALL TWO-LAYER TESTS PASSED")
