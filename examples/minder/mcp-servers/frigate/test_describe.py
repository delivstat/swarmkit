"""Standalone tests for the cloud describe path (MINDER_DESCRIBE_PROVIDER=openrouter).

Pins: the cloud VLM helper (`_describe_via_cloud`) parsing an OpenAI-compatible reply
and degrading gracefully (no key / error → ""), and `_describe_snapshot`'s provider
routing — cloud when configured, falling back to the local VLM on a cloud miss so an
alert never loses its description.

Run in-container:  docker compose exec -T minder python /app/mcp-servers/frigate/test_describe.py
"""

import io
import json
import tempfile
from pathlib import Path

import server as f


class _Resp(io.BytesIO):
    """Minimal urlopen() return — a context-free object with .read()."""


def _fake_urlopen(body: dict):
    def _open(req, timeout=0):
        return _Resp(json.dumps(body).encode())

    return _open


def test_cloud_describe_parses_openai_reply():
    f.OPENROUTER_KEY = "test-key"
    orig = f.urllib.request.urlopen
    f.urllib.request.urlopen = _fake_urlopen(
        {"choices": [{"message": {"content": "A man in a red jacket at the gate."}}]}
    )
    try:
        out = f._describe_via_cloud("aGVsbG8=", "describe")
    finally:
        f.urllib.request.urlopen = orig
    assert out == "A man in a red jacket at the gate.", out
    print("ok  cloud describe parses the OpenAI-compatible reply")


def test_cloud_describe_no_key_is_empty():
    orig_key = f.OPENROUTER_KEY
    f.OPENROUTER_KEY = ""
    try:
        assert f._describe_via_cloud("aGVsbG8=", "describe") == ""
    finally:
        f.OPENROUTER_KEY = orig_key
    print("ok  cloud describe with no key is a graceful empty string")


def test_cloud_describe_error_is_empty():
    f.OPENROUTER_KEY = "test-key"
    orig = f.urllib.request.urlopen

    def _boom(req, timeout=0):
        raise OSError("network down")

    f.urllib.request.urlopen = _boom
    try:
        assert f._describe_via_cloud("aGVsbG8=", "describe") == ""
    finally:
        f.urllib.request.urlopen = orig
    print("ok  cloud describe swallows errors (graceful)")


def test_snapshot_routes_to_cloud_then_falls_back():
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(b"\xff\xd8\xff\xe0jpeg-bytes")
        path = tmp.name

    f.DESCRIBE_PROVIDER = "openrouter"
    f.OPENROUTER_KEY = "test-key"
    orig_cloud = f._describe_via_cloud
    orig_open = f.urllib.request.urlopen
    # Local Ollama path returns "LOCAL" (message.content shape).
    f.urllib.request.urlopen = _fake_urlopen({"message": {"content": "LOCAL"}})
    try:
        f._describe_via_cloud = lambda img, prompt: "CLOUD"  # type: ignore
        assert f._describe_snapshot(path, label="person") == "CLOUD"

        f._describe_via_cloud = lambda img, prompt: ""  # type: ignore  # cloud miss
        assert f._describe_snapshot(path, label="person") == "LOCAL"
    finally:
        f._describe_via_cloud = orig_cloud  # type: ignore
        f.urllib.request.urlopen = orig_open
        Path(path).unlink(missing_ok=True)
    print("ok  _describe_snapshot uses cloud, falls back to local on a cloud miss")


if __name__ == "__main__":
    test_cloud_describe_parses_openai_reply()
    test_cloud_describe_no_key_is_empty()
    test_cloud_describe_error_is_empty()
    test_snapshot_routes_to_cloud_then_falls_back()
    print("\nALL DESCRIBE TESTS PASSED")
