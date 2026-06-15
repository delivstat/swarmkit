"""Minder Camera MCP Server — network discovery, ONVIF probe, RTSP snapshot.

Exposes tools for scanning the local network, discovering cameras via ONVIF,
capturing snapshots via ffmpeg, and managing camera names.
"""

import base64
import datetime
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, "/app/mcp-servers")
import contextlib

from _alert_sink import write_alert
from _atomic import write_json_atomic

mcp = FastMCP("minder-camera")

SUBNET = os.environ.get("MINDER_SUBNET", "192.168.0")
CAM_USER = os.environ.get("MINDER_CAM_USER", "admin")
CAM_PASS = os.environ.get("MINDER_CAM_PASS", "admin123")
DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
CAMERAS_FILE = DATA_DIR / "cameras.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
RULES_FILE = DATA_DIR / "rules.json"
MONITOR_STATE_FILE = DATA_DIR / "monitor_state.json"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VISION_MODEL = os.environ.get("MINDER_VISION_MODEL", "llava-phi3")
ALERT_COOLDOWN_S = 600  # don't re-alert the same rule+camera within 10 min

CAMERA_PORTS = {554: "RTSP", 8554: "RTSP-alt", 80: "HTTP", 8080: "HTTP-alt"}


@dataclass
class Camera:
    ip: str
    manufacturer: str = "unknown"
    model: str = "unknown"
    firmware: str = "unknown"
    serial: str = "unknown"
    rtsp_url: str = ""
    snapshot_url: str = ""
    name: str = ""
    onvif: bool = False
    osd_name: str = ""
    # Perception tier: "frigate" (RTSP → Frigate pipeline) or "ha-snapshot"
    # (cloud-locked cameras surfaced via Home Assistant, Phase 3).
    tier: str = ""
    ha_entity: str = ""


def _load_cameras() -> list[Camera]:
    if not CAMERAS_FILE.exists():
        return []
    return [Camera(**c) for c in json.loads(CAMERAS_FILE.read_text())]


def _save_cameras(cameras: list[Camera]) -> None:
    write_json_atomic(CAMERAS_FILE, [asdict(c) for c in cameras])


def _onvif_auth_header(username: str, password: str) -> str:
    nonce = os.urandom(16)
    created = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest_input = nonce + created.encode() + password.encode()
    digest = base64.b64encode(hashlib.sha1(digest_input).digest()).decode()
    nonce_b64 = base64.b64encode(nonce).decode()
    return (
        '<Security xmlns="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        "<UsernameToken>"
        f"<Username>{username}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</Password>"
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{nonce_b64}</Nonce>"
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"{created}</Created>"
        "</UsernameToken></Security>"
    )


def _onvif_request(ip: str, path: str, body: str) -> str:
    auth = _onvif_auth_header(CAM_USER, CAM_PASS)
    soap = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
        ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl">'
        f"<soap:Header>{auth}</soap:Header>"
        f"<soap:Body>{body}</soap:Body>"
        "</soap:Envelope>"
    )
    conn = http.client.HTTPConnection(ip, 80, timeout=5)
    conn.request(
        "POST", path, body=soap, headers={"Content-Type": "application/soap+xml; charset=utf-8"}
    )
    resp = conn.getresponse()
    result = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return result


def _get_osd_name(ip: str) -> str:
    """Try to extract the OSD channel name from the camera."""
    try:
        body = _onvif_request(ip, "/onvif/media_service", "<trt:GetOSDs/>")
        # Look for channel name in OSD text
        text_match = re.search(r"<tt:PlainText>(.*?)</tt:PlainText>", body)
        if text_match:
            return text_match.group(1).strip()
        # Fallback: look in VideoSourceConfiguration name
        body2 = _onvif_request(ip, "/onvif/media_service", "<trt:GetVideoSourceConfigurations/>")
        name_match = re.search(r'<trt:VideoSourceConfiguration[^>]*Name="([^"]+)"', body2)
        if name_match:
            return name_match.group(1).strip()
    except Exception:
        pass
    return ""


def _probe_camera(ip: str) -> Camera | None:
    cam = Camera(ip=ip)
    try:
        body = _onvif_request(ip, "/onvif/device_service", "<tds:GetDeviceInformation/>")
        if "NotAuthorized" in body:
            return None
        for field in ["Manufacturer", "Model", "FirmwareVersion", "SerialNumber"]:
            m = re.search(rf"<[^>]*{field}[^>]*>(.*?)</", body)
            if m:
                attr = {"FirmwareVersion": "firmware", "SerialNumber": "serial"}.get(
                    field, field.lower()
                )
                setattr(cam, attr, m.group(1))
        cam.onvif = True
    except Exception:
        return None

    # Stream URI
    try:
        profiles = _onvif_request(ip, "/onvif/media_service", "<trt:GetProfiles/>")
        tokens = re.findall(r'token="([^"]+)"', profiles)
        if tokens:
            stream_body = (
                "<trt:GetStreamUri><trt:StreamSetup>"
                '<tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>'
                '<tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema">'
                "<tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
                f"</trt:StreamSetup><trt:ProfileToken>{tokens[0]}</trt:ProfileToken>"
                "</trt:GetStreamUri>"
            )
            uri_body = _onvif_request(ip, "/onvif/media_service", stream_body)
            uri = re.search(r"<[^>]*Uri[^>]*>(rtsp://[^<]+)</", uri_body)
            if uri:
                cam.rtsp_url = uri.group(1).replace("&amp;", "&")
    except Exception:
        pass

    cam.osd_name = _get_osd_name(ip)
    if cam.osd_name and not cam.name:
        cam.name = cam.osd_name

    return cam


def _capture_snapshot(cam: Camera) -> Path | None:
    if not cam.rtsp_url:
        return None
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    outpath = SNAPSHOT_DIR / f"{cam.ip.replace('.', '_')}.jpg"
    authed_url = cam.rtsp_url.replace("rtsp://", f"rtsp://{CAM_USER}:{CAM_PASS}@")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-rtsp_transport",
                "tcp",
                "-i",
                authed_url,
                "-frames:v",
                "1",
                "-y",
                str(outpath),
            ],
            capture_output=True,
            timeout=10,
        )
        if outpath.exists() and outpath.stat().st_size > 1000:
            return outpath
    except Exception:
        pass
    return None


@mcp.tool()
def scan_network(subnet: str = "") -> str:
    """Scan the local network for cameras. Returns a list of IPs with camera-related ports open.
    Leave subnet empty to use the configured default."""
    if not isinstance(subnet, str) or not subnet.strip() or not subnet[0].isdigit():
        subnet = SUBNET
    results = []

    def check(ip: str) -> str | None:
        for port in CAMERA_PORTS:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.8)
                if s.connect_ex((ip, port)) == 0:
                    s.close()
                    return ip
                s.close()
            except OSError:
                pass
        return None

    hosts = [f"{subnet}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=100) as pool:
        futures = {pool.submit(check, ip): ip for ip in hosts}
        for f in as_completed(futures):
            ip = f.result()
            if ip and ip not in results:
                results.append(ip)

    results.sort(key=lambda x: int(x.split(".")[-1]))
    return json.dumps({"hosts": results, "count": len(results)})


@mcp.tool()
def discover_cameras(subnet: str = "") -> str:
    """Full discovery: scan network, probe ONVIF, capture snapshots, extract OSD names.
    Returns the complete camera inventory. Leave subnet empty to use the configured default."""
    if not isinstance(subnet, str) or not subnet.strip() or not subnet[0].isdigit():
        subnet = SUBNET
    scan_result = json.loads(scan_network(subnet))

    cameras = []
    for ip in scan_result["hosts"]:
        cam = _probe_camera(ip)
        if cam and cam.onvif and cam.rtsp_url:
            # RTSP + ONVIF → the Frigate perception tier. Cloud-locked cameras
            # (no local stream) enter as ha-snapshot via Home Assistant (Phase 3).
            cam.tier = "frigate"
            _capture_snapshot(cam)
            cameras.append(cam)

    # Preserve existing names + tier overrides across re-discovery
    old_cameras = _load_cameras()
    old_names = {c.ip: c.name for c in old_cameras if c.name}
    old_tiers = {c.ip: (c.tier, c.ha_entity) for c in old_cameras if c.tier}
    for cam in cameras:
        if cam.ip in old_names and not cam.name:
            cam.name = old_names[cam.ip]
        if cam.ip in old_tiers:
            cam.tier, cam.ha_entity = old_tiers[cam.ip]

    _save_cameras(cameras)
    # Return concise summary — full JSON overwhelms small models
    summary = [
        f"{i + 1}. {c.name or c.ip} — {c.manufacturer} {c.model}" for i, c in enumerate(cameras)
    ]
    return json.dumps(
        {
            "count": len(cameras),
            "cameras": summary,
        }
    )


@mcp.tool()
def list_cameras() -> str:
    """List all discovered cameras with their names, models, and IPs."""
    cameras = _load_cameras()
    summary = []
    for i, cam in enumerate(cameras, 1):
        summary.append(
            {
                "index": i,
                "ip": cam.ip,
                "name": cam.name or "(unnamed)",
                "manufacturer": cam.manufacturer,
                "model": cam.model,
                "osd_name": cam.osd_name,
                "has_rtsp": bool(cam.rtsp_url),
            }
        )
    return json.dumps(summary)


@mcp.tool()
def name_camera(camera_ip: str, name: str) -> str:
    """Set a friendly name for a camera by its IP address."""
    cameras = _load_cameras()
    for cam in cameras:
        if cam.ip == camera_ip:
            cam.name = name
            _save_cameras(cameras)
            return json.dumps({"status": "ok", "ip": camera_ip, "name": name})
    return json.dumps({"status": "error", "message": f"Camera {camera_ip} not found"})


@mcp.tool()
def name_cameras_bulk(assignments: str) -> str:
    """Name multiple cameras at once. Format: '1=porch, 2=backyard, 3=gate'
    where numbers are the camera index from list_cameras."""
    cameras = _load_cameras()
    named = []
    for part in assignments.split(","):
        if "=" not in part:
            continue
        idx_str, name = part.split("=", 1)
        try:
            idx = int(idx_str.strip()) - 1
            if 0 <= idx < len(cameras):
                cameras[idx].name = name.strip()
                named.append({"ip": cameras[idx].ip, "name": name.strip()})
        except ValueError:
            continue
    _save_cameras(cameras)
    return json.dumps({"named": named, "count": len(named)})


@mcp.tool()
def capture_camera_snapshot(camera_identifier: str) -> str:
    """Capture a fresh snapshot from a camera. Accepts camera name or IP.
    Fuzzy matches: 'porch' matches 'Porch-1', 'main door' matches 'Main-Door'."""
    target = _find_camera(camera_identifier)
    if not target:
        return json.dumps({"status": "error", "message": f"Camera '{camera_identifier}' not found"})

    snap = _capture_snapshot(target)
    if snap and snap.exists():
        return json.dumps(
            {
                "status": "ok",
                "camera": target.name or target.ip,
                "path": str(snap),
            }
        )
    return json.dumps({"status": "error", "message": "Failed to capture snapshot"})


def _find_camera(identifier: str) -> Camera | None:
    """Find a camera by exact name or IP. The reasoning agent resolves
    fuzzy names via list-cameras before calling this."""
    cameras = _load_cameras()
    ident_lower = identifier.lower().strip()
    for cam in cameras:
        if cam.name.lower() == ident_lower or cam.ip == ident_lower:
            return cam
    for cam in cameras:
        if ident_lower in cam.name.lower():
            return cam
    return None


def capture_video_clip(cam: Camera, duration: int = 8) -> Path | None:
    """Capture a short video clip from a camera's RTSP stream."""
    if not cam.rtsp_url:
        return None
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    outpath = SNAPSHOT_DIR / f"{cam.ip.replace('.', '_')}_clip.mp4"
    authed_url = cam.rtsp_url.replace("rtsp://", f"rtsp://{CAM_USER}:{CAM_PASS}@")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-rtsp_transport",
                "tcp",
                "-i",
                authed_url,
                "-t",
                str(duration),
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "28",
                "-vf",
                "scale=640:-2",
                "-an",
                "-y",
                str(outpath),
            ],
            capture_output=True,
            timeout=duration + 15,
        )
        if outpath.exists() and outpath.stat().st_size > 5000:
            return outpath
    except Exception:
        pass
    return None


@mcp.tool()
def capture_camera_video(camera_identifier: str, duration: int = 8) -> str:
    """Capture a short video clip (5-10 seconds) from a camera's live RTSP feed.
    Use this when the user asks to see a live feed, video, or stream."""
    cam = _find_camera(camera_identifier)
    if not cam:
        return json.dumps({"status": "error", "message": f"Camera '{camera_identifier}' not found"})

    clip = capture_video_clip(cam, duration)
    if clip and clip.exists():
        return json.dumps(
            {
                "status": "ok",
                "camera_ip": cam.ip,
                "camera_name": cam.name,
                "path": str(clip),
                "duration": duration,
                "size_kb": clip.stat().st_size // 1024,
            }
        )
    return json.dumps({"status": "error", "message": "Failed to capture video"})


@mcp.tool()
def send_telegram_alert(message: str, camera_name: str = "") -> str:
    """Send an alert notification via Telegram and save as an event.
    Captures a fresh snapshot and short video clip for the event record."""
    return _create_alert(message, camera_name)


def _create_alert(message: str, camera_name: str = "") -> str:
    """Write an alert (Telegram queue) + event (dashboard) with fresh media.
    Captures fresh RTSP media here, then hands off to the shared sink so the
    YOLO path and the Frigate poller produce identical alert/event records."""
    import uuid as _uuid

    event_id = _uuid.uuid4().hex[:8]
    event_dir = DATA_DIR / "events" / event_id
    event_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = ""
    video_path = ""
    cam = _find_camera(camera_name) if camera_name else None

    if cam:
        import shutil as _shutil

        snap = _capture_snapshot(cam)
        if snap and snap.exists():
            event_snap = event_dir / "snapshot.jpg"
            _shutil.copy2(str(snap), str(event_snap))
            snapshot_path = str(event_snap)

        clip = capture_video_clip(cam, duration=5)
        if clip and clip.exists():
            event_clip = event_dir / "clip.mp4"
            _shutil.copy2(str(clip), str(event_clip))
            video_path = str(event_clip)

    write_alert(message, camera_name, snapshot_path, video_path, event_id=event_id)
    return json.dumps({"status": "ok", "event_id": event_id})


# ---- Monitoring (deterministic — no LLM in the loop) ----

# YOLO object detector is shared with the detect MCP server.
sys.path.insert(0, "/app/mcp-servers/detect")
try:
    import detector as _yolo
except Exception:  # pragma: no cover - detector optional at import time
    _yolo = None


def _normalize_time(s: str) -> str:
    s = (s or "").strip().lower().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not m:
        return ""
    h, minute = int(m[1]), int(m[2] or 0)
    if m[3] == "pm" and h != 12:
        h += 12
    if m[3] == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= minute < 60):
        return ""
    return f"{h:02d}:{minute:02d}"


@mcp.tool()
def create_monitoring_rule(
    condition: str = "",
    camera: str = "all",
    schedule: str = "always",
    device: str = "",
    device_action: Literal["turn_on", "turn_off", ""] = "",
    alert: Literal["yes", "no"] = "yes",
    at_time: str = "",
) -> str:
    """Create an automation rule. Two kinds:
    1. Vision rule — watches a camera for a condition and runs actions
       when it matches (set condition).
    2. Time rule — runs actions every day at a fixed time (set at_time,
       leave condition empty).

    condition: short visual question, e.g. "is there a person" ("" for time rules)
    camera: camera name from the CAMERAS list, or "all" (vision rules only)
    schedule: "always", "night", "day", or a 24h range like "22:00-06:00"
    device: smart device to control (optional)
    device_action: turn_on or turn_off (required if device is set)
    alert: yes to notify the user, no for silent device-only scenarios
    at_time: daily trigger time like "04:00" or "4am" (time rules only)
    """
    at_time = _normalize_time(at_time)
    if not (condition and condition.strip()) and not at_time:
        return json.dumps(
            {"status": "error", "message": "need a condition (vision rule) or at_time (time rule)"}
        )

    # Fuzzy-match camera against the inventory
    cam_name = "all"
    requested = (camera or "all").lower().strip()
    if requested not in ("", "all", "any", "all cameras"):
        req_words = set(re.findall(r"[a-z0-9]+", requested))
        best_score = 0
        for c in _load_cameras():
            name = c.name or c.ip
            words = set(re.findall(r"[a-z0-9]+", name.lower()))
            score = len(req_words & words)
            if score > best_score:
                cam_name, best_score = name, score

    schedule = (schedule or "always").strip()
    if not re.fullmatch(r"always|night|day|\d{1,2}:\d{2}-\d{1,2}:\d{2}", schedule):
        schedule = "always"

    actions: list[dict] = []
    if str(alert).lower() not in ("no", "false", "0") or not device:
        actions.append({"type": "alert"})
    if device and device_action in ("turn_on", "turn_off"):
        actions.append({"type": "device", "device": device, "action": device_action})

    rule = {
        "camera": cam_name,
        "condition": (condition or "").strip(),
        "schedule": schedule,
        "enabled": True,
        "actions": actions,
        "at_time": at_time,
        "created_ts": time.time(),
    }

    rules = []
    if RULES_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            rules = json.loads(RULES_FILE.read_text())

    # Dedup — agents may call this tool repeatedly within one run
    for existing in rules:
        if (
            existing.get("camera") == rule["camera"]
            and existing.get("condition") == rule["condition"]
            and existing.get("schedule") == rule["schedule"]
            and existing.get("at_time", "") == rule["at_time"]
        ):
            return json.dumps({"status": "ok", "rule": existing, "note": "rule already exists"})

    rules.append(rule)
    write_json_atomic(RULES_FILE, rules)

    return json.dumps({"status": "ok", "rule": rule})


if __name__ == "__main__":
    mcp.run(transport="stdio")
