"""Standalone test for deterministic video (clip grab without an agent).

A `kind: video` plan is executed by code: _grab_clips captures a short live clip
per camera via the camera server's capture_camera_video (ffmpeg from RTSP), no
minder-video agent. This pins that contract with a faked camera module.

Run in-container:  docker compose exec -T minder python /app/webapp/test_video.py
"""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import minder_ops as ops


def _fake_camera(tmpdir, record, fail=False):
    """Fake camera module: capture_camera_video writes a stub mp4 and returns its
    path (or an error envelope when fail=True). Records the cameras requested."""

    def capture_camera_video(camera, duration=8):
        record.append((camera, duration))
        if fail:
            return json.dumps({"status": "error", "message": "no stream"})
        p = Path(tmpdir) / f"{camera.replace(' ', '_')}_clip.mp4"
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 6000)  # plausible mp4
        return json.dumps({"status": "ok", "camera_name": camera, "path": str(p)})

    ops._camera_module = lambda: SimpleNamespace(capture_camera_video=capture_camera_video)  # type: ignore


def test_grab_clip_returns_path():
    rec = []
    tmp = tempfile.mkdtemp()
    _fake_camera(tmp, rec)
    p = ops._grab_clip("Main Door")
    assert rec == [("Main Door", 8)]  # exactly one ffmpeg grab, default duration
    assert p and p.exists() and p.suffix == ".mp4"
    print("ok  _grab_clip captures one clip per camera")


def test_grab_clips_multiple():
    rec = []
    tmp = tempfile.mkdtemp()
    _fake_camera(tmp, rec)
    clips = ops._grab_clips(["Porch", "Gate"])
    assert [c for c, _ in rec] == ["Porch", "Gate"]
    assert len(clips) == 2 and all(c.exists() for c in clips)
    print("ok  _grab_clips captures each requested camera")


def test_grab_clip_failure_is_none():
    rec = []
    tmp = tempfile.mkdtemp()
    _fake_camera(tmp, rec, fail=True)
    assert ops._grab_clip("Backyard") is None  # graceful on capture failure
    assert ops._grab_clips(["Backyard", "Office"]) == []
    print("ok  _grab_clip(s) graceful on capture failure")


if __name__ == "__main__":
    test_grab_clip_returns_path()
    test_grab_clips_multiple()
    test_grab_clip_failure_is_none()
    print("\nALL VIDEO TESTS PASSED")
