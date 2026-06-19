"""Standalone test for the Discord adapter's alert/media delivery.

Pins the regression that caused "only an image, no clip": an empty snapshot_path
or video_path used to become Path("") == Path(".") whose .exists() is True, so the
adapter tried to upload the cwd — raising. On a clip-only alert that raise happened
BEFORE the clip was sent, so clips never arrived. Empty paths must be skipped.

Run in-container:  docker compose exec -T minder python /app/test_discord_alert.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")

import discord_bot as db


class _FakeChannel:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.files: list[str] = []

    async def send(self, content=None, file=None):  # noqa: ANN001
        if file is not None:
            self.files.append(str(file))
        elif content is not None:
            self.texts.append(content)


def _run_alert(alert: dict) -> _FakeChannel:
    chan = _FakeChannel()
    db._load_channel = lambda: {"channel_id": 1}
    db.client.get_channel = lambda _id: chan  # type: ignore[method-assign]
    db.discord.File = lambda p: p  # type: ignore[assignment]  # identity: record the path
    asyncio.run(db._deliver_alert(alert))
    return chan


def test_clip_only_alert_sends_clip():
    with tempfile.TemporaryDirectory() as d:
        clip = Path(d) / "evt.mp4"
        clip.write_bytes(b"x" * 2000)
        chan = _run_alert({"message": "", "snapshot_path": "", "video_path": str(clip)})
    assert chan.files == [str(clip)], chan.files
    assert chan.texts == [], chan.texts
    print("ok  clip-only alert (empty snapshot_path) still delivers the clip")


def test_snapshot_only_alert_sends_image():
    with tempfile.TemporaryDirectory() as d:
        snap = Path(d) / "evt.jpg"
        snap.write_bytes(b"x" * 2000)
        chan = _run_alert({"message": "", "snapshot_path": str(snap), "video_path": ""})
    assert chan.files == [str(snap)], chan.files
    print("ok  snapshot-only alert (empty video_path) delivers the image, no crash")


def test_text_plus_both_media():
    with tempfile.TemporaryDirectory() as d:
        snap, clip = Path(d) / "e.jpg", Path(d) / "e.mp4"
        snap.write_bytes(b"x" * 2000)
        clip.write_bytes(b"x" * 2000)
        chan = _run_alert(
            {"message": "person at gate", "snapshot_path": str(snap), "video_path": str(clip)}
        )
    assert chan.texts == ["🚨 person at gate"], chan.texts
    assert chan.files == [str(snap), str(clip)], chan.files
    print("ok  full alert delivers text + image + clip in order")


if __name__ == "__main__":
    test_clip_only_alert_sends_clip()
    test_snapshot_only_alert_sends_image()
    test_text_plus_both_media()
    print("\nALL DISCORD-ALERT TESTS PASSED")
