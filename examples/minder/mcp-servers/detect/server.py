"""Minder Detection MCP Server — YOLO object detection for cameras.

Exposes fast, reliable people/vehicle/animal detection over camera snapshots.
This is the workhorse for monitoring and for "is anyone there" checks —
purpose-built object detection, not a general vision-language model.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import detector

mcp = FastMCP("minder-detect")

DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
CAMERAS_FILE = DATA_DIR / "cameras.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CAM_USER = os.environ.get("MINDER_CAM_USER", "admin")
CAM_PASS = os.environ.get("MINDER_CAM_PASS", "admin123")


def _record_check(camera: str, condition: str, match: bool, scene: str) -> None:
    """Record the detection so the channel adapter can format a fallback
    reply if the agent's own text isn't usable."""
    f = DATA_DIR / "vision_checks.json"
    checks = []
    if f.exists():
        try:
            checks = json.loads(f.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    checks.append({"ts": time.time(), "camera": camera, "condition": condition,
                   "match": match, "answer": scene})
    f.write_text(json.dumps(checks[-50:], indent=2))


def _find_camera(identifier: str) -> dict | None:
    if not CAMERAS_FILE.exists():
        return None
    cameras = json.loads(CAMERAS_FILE.read_text())
    ident = (identifier or "").lower().strip()
    for cam in cameras:
        if cam.get("name", "").lower() == ident or cam.get("ip") == ident:
            return cam
    for cam in cameras:
        if ident in cam.get("name", "").lower():
            return cam
    # Tolerate mangled args (small models sometimes pass a stringified dict
    # like "{'type': 'Main-Door', ...}"): match if the camera name or its
    # word-set appears inside the identifier.
    ident_words = set(re.findall(r"[a-z0-9]+", ident))
    best, best_score = None, 0
    for cam in cameras:
        name = cam.get("name", "").lower()
        if name and name in ident:
            return cam
        score = len(ident_words & set(re.findall(r"[a-z0-9]+", name)))
        if score > best_score:
            best, best_score = cam, score
    return best


def _fresh_snapshot(cam: dict) -> Path | None:
    if not cam.get("rtsp_url"):
        return None
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOT_DIR / f"{cam['ip'].replace('.', '_')}.jpg"
    url = cam["rtsp_url"].replace("rtsp://", f"rtsp://{CAM_USER}:{CAM_PASS}@")
    try:
        subprocess.run(
            ["ffmpeg", "-rtsp_transport", "tcp", "-i", url,
             "-frames:v", "1", "-y", str(out)],
            capture_output=True, timeout=10)
        if out.exists() and out.stat().st_size > 1000:
            return out
    except Exception:
        pass
    return None


@mcp.tool()
def detect_people(camera: str) -> str:
    """Detect whether any people are present in a camera's view right now.
    Captures a fresh snapshot and runs object detection. Returns the count
    of people and a confidence score — reliable for security monitoring."""
    cam = _find_camera(camera)
    if not cam:
        return json.dumps({"status": "error", "message": f"Camera '{camera}' not found"})
    snap = _fresh_snapshot(cam)
    if not snap:
        existing = SNAPSHOT_DIR / f"{cam['ip'].replace('.', '_')}.jpg"
        snap = existing if existing.exists() else None
    if not snap:
        return json.dumps({"status": "error", "message": "No snapshot available"})

    res = detector.detect(str(snap), want_classes=detector.PERSON_CLASSES)
    people = res["matched"]
    return json.dumps({
        "camera": cam.get("name", cam["ip"]),
        "people": people,
        "present": people > 0,
        "confidence": res["best_confidence"],
        "also_seen": detector.describe_counts(res["counts"]),
    })


@mcp.tool()
def detect_objects(camera: str, looking_for: str = "person") -> str:
    """Detect people, vehicles, or animals in a camera's view.
    looking_for: free text like 'person', 'car', 'dog', 'any animal'.
    Returns whether a matching object was found, with counts and confidence."""
    cam = _find_camera(camera)
    if not cam:
        return json.dumps({"status": "error", "message": f"Camera '{camera}' not found"})
    snap = _fresh_snapshot(cam)
    if not snap:
        existing = SNAPSHOT_DIR / f"{cam['ip'].replace('.', '_')}.jpg"
        snap = existing if existing.exists() else None
    if not snap:
        return json.dumps({"status": "error", "message": "No snapshot available"})

    want = detector.classes_for_condition(looking_for) or detector.PERSON_CLASSES
    res = detector.detect(str(snap), want_classes=want)
    cam_name = cam.get("name", cam["ip"])
    scene = detector.describe_counts(res["counts"])
    _record_check(cam_name, looking_for, res["matched"] > 0, scene)
    return json.dumps({
        "camera": cam_name,
        "looking_for": looking_for,
        "found": res["matched"] > 0,
        "count": res["matched"],
        "confidence_pct": int(res["best_confidence"] * 100),
        "everything_seen": scene or "nothing",
    })


if __name__ == "__main__":
    mcp.run(transport="stdio")
