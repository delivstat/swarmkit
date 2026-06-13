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
import urllib.request
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
VISION_MODEL = os.environ.get("MINDER_VISION_MODEL", "granite3.2-vision")
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


def _vision_condition(snapshot: Path, condition: str) -> tuple[bool, str]:
    """Evaluate a condition on a snapshot.

    Object-presence conditions (person / vehicle / animal) use YOLO — fast,
    CPU-only, and far more reliable than a VLM for surveillance. Everything
    else (e.g. "is the gate open") falls back to the local vision model.
    """
    want = _yolo.classes_for_condition(condition) if _yolo else set()
    if want:
        res = _yolo.detect(str(snapshot), want_classes=want)
        if res["matched"] > 0:
            return (
                True,
                f"detected {res['matched']} ({res['best_confidence']:.0%} conf); scene: {_yolo.describe_counts(res['counts'])}",
            )
        return False, f"none detected; scene: {_yolo.describe_counts(res['counts'])}"

    # Non-object condition → vision-language model
    img_b64 = base64.b64encode(snapshot.read_bytes()).decode()
    payload = json.dumps(
        {
            "model": VISION_MODEL,
            "prompt": f"Look at this security camera image. {condition}? Describe what you see.",
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200},
        }
    ).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=payload, headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=120)
    answer = json.loads(resp.read()).get("response", "").strip()

    low = answer.lower()
    yes_signals = [
        "yes",
        "there is",
        "there are",
        "visible",
        "can see",
        "detected",
        "present",
        "appears to be",
    ]
    no_signals = ["no ", "not ", "cannot", "don't", "empty", "no one", "nobody", "none"]
    match = any(s in low for s in yes_signals) and not any(s in low for s in no_signals)
    return match, answer


def _schedule_active(schedule: str) -> bool:
    if not schedule or schedule == "always":
        return True
    now_t = datetime.datetime.now()  # container-local time (set TZ env)
    hour = now_t.hour
    if schedule == "night":
        return hour >= 20 or hour < 6
    if schedule == "day":
        return 6 <= hour < 20
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", schedule)
    if m:
        now_min = hour * 60 + now_t.minute
        start = int(m[1]) * 60 + int(m[2])
        end = int(m[3]) * 60 + int(m[4])
        if start <= end:
            return start <= now_min < end
        return now_min >= start or now_min < end  # range wraps midnight
    return False  # "away" needs presence detection — not supported yet


def _load_monitor_state() -> dict:
    if MONITOR_STATE_FILE.exists():
        try:
            return json.loads(MONITOR_STATE_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


OPS_URL = os.environ.get("MINDER_API_URL", "http://localhost:80")
INTERNAL_TOKEN_FILE = DATA_DIR / "internal_token"


def _execute_device_action(device: str, action: str) -> str:
    """Run a scenario device action through the ops API (single source of
    truth for device matching + HA control)."""
    token = ""
    if INTERNAL_TOKEN_FILE.exists():
        token = INTERNAL_TOKEN_FILE.read_text().strip()
    phrase = ("turn on " if action == "turn_on" else "turn off ") + device
    req = urllib.request.Request(
        f"{OPS_URL}/api/ops/message",
        data=json.dumps({"text": phrase, "source": "monitor"}).encode(),
        headers={"Content-Type": "application/json", "X-Minder-Internal": token},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=20)
    return json.loads(resp.read()).get("text", "")


ROUTES_FILE = DATA_DIR / "route_decisions.json"
VALID_INTENTS = {
    "scenario",
    "vision",
    "snapshot",
    "video",
    "device",
    "device_list",
    "camera_list",
    "chat",
}


@mcp.tool()
def route_request(
    intent: Literal[
        "scenario", "vision", "snapshot", "video", "device", "device_list", "camera_list", "chat"
    ],
    cleaned_request: str = "",
) -> str:
    """Record what kind of request the user made so the right specialist
    handles it.

    intent:
      scenario    — create an automation rule ("when X happens, do Y")
      vision      — check what a camera sees right now ("is anyone outside?")
      snapshot    — send a photo from a camera
      video       — send a live video clip from a camera
      device      — turn a smart device on or off right now
      device_list — list the smart devices (lights, switches, fans)
      camera_list — list or name the cameras
      chat        — anything else
    cleaned_request: the user's request restated clearly in one sentence.
    """
    intent = (intent or "").strip().lower()
    if intent not in VALID_INTENTS:
        intent = "chat"
    routes = []
    if ROUTES_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            routes = json.loads(ROUTES_FILE.read_text())
    routes.append({"ts": time.time(), "intent": intent, "cleaned_request": cleaned_request})
    ROUTES_FILE.write_text(json.dumps(routes[-50:], indent=2))
    return json.dumps({"status": "ok", "intent": intent})


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


@mcp.tool()
def run_monitoring_rules(rules: str = "") -> str:
    """Run all enabled monitoring rules once: capture snapshots, check each
    rule's condition with the vision model, create alerts for matches.
    The rules argument is ignored (rules come from configuration).
    Fully deterministic — call exactly once per monitoring cycle."""
    if not RULES_FILE.exists():
        return json.dumps({"status": "idle", "reason": "no rules configured"})
    try:
        rules = json.loads(RULES_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"status": "idle", "reason": "rules file unreadable"})

    active = [
        r for r in rules if r.get("enabled", True) and _schedule_active(r.get("schedule", "always"))
    ]
    if not active:
        return json.dumps({"status": "idle", "reason": "no active rules"})

    cameras = _load_cameras()
    if not cameras:
        return json.dumps({"status": "idle", "reason": "no cameras"})

    state = _load_monitor_state()
    now = time.time()

    # Throttle: the agent may call this tool several times per run, and
    # cycles can exceed the cron interval — both become cheap no-ops.
    if now - state.get("_last_run", 0) < 45:
        return json.dumps({"status": "skipped", "reason": "ran recently"})
    if now - state.get("_running_since", 0) < 180:
        return json.dumps({"status": "skipped", "reason": "previous cycle still running"})
    state["_last_run"] = now
    state["_running_since"] = now
    write_json_atomic(MONITOR_STATE_FILE, state)

    checked, fired = 0, []
    try:
        for rule in active:
            if rule.get("target") == "ha":
                continue  # compiled to a native HA automation — HA fires it
            # Time rules: fire once daily within a 5-minute window of at_time
            at_time = rule.get("at_time") or ""
            if at_time:
                now_local = datetime.datetime.now()
                try:
                    h, m = map(int, at_time.split(":"))
                except ValueError:
                    continue
                delta = (now_local.hour * 60 + now_local.minute) - (h * 60 + m)
                if not (0 <= delta < 5):
                    continue
                key = f"time|{at_time}|{json.dumps(rule.get('actions', []))[:100]}"
                if now - state.get(key, 0) < 20 * 3600:
                    continue
                state[key] = now
                action_notes = []
                for act in rule.get("actions", []):
                    if act.get("type") == "device":
                        try:
                            action_notes.append(
                                _execute_device_action(act["device"], act["action"])
                            )
                        except Exception as e:
                            action_notes.append(f"FAILED {act.get('device')}: {e}")
                if any(a.get("type") == "alert" for a in rule.get("actions", [])) or action_notes:
                    msg = f"Scheduled ({at_time}): " + ("; ".join(action_notes) or "alert")
                    _create_alert(msg, "")
                fired.append(key)
                continue

            rule_cams = rule.get("cameras") or [rule.get("camera", "all")]
            rule_cams = [str(rc).lower() for rc in rule_cams]
            if any(rc in ("all", "") for rc in rule_cams):
                targets = cameras
            else:
                targets = [
                    c
                    for c in cameras
                    if any(rc in (c.name or "").lower() or c.ip == rc for rc in rule_cams)
                ]
            for cam in targets:
                snap = _capture_snapshot(cam)
                if not snap:
                    continue
                try:
                    match, answer = _vision_condition(snap, rule["condition"])
                except Exception:
                    continue
                checked += 1
                if not match:
                    continue
                key = f"{cam.name or cam.ip}|{rule['condition']}"
                if now - state.get(key, 0) < ALERT_COOLDOWN_S:
                    continue
                state[key] = now

                actions = rule.get("actions") or [{"type": "alert"}]
                action_notes = []
                for act in actions:
                    if act.get("type") == "device":
                        try:
                            note = _execute_device_action(act["device"], act["action"])
                            action_notes.append(note)
                        except Exception as e:
                            action_notes.append(f"❌ {act.get('device')}: {e}")

                wants_alert = any(a.get("type") == "alert" for a in actions)
                if wants_alert or action_notes:
                    msg = f"{rule['condition']} — {cam.name or cam.ip}: {answer[:150]}"
                    if action_notes:
                        msg += "\n" + "\n".join(action_notes)
                    _create_alert(msg, cam.name or cam.ip)
                fired.append(key)
    finally:
        state["_running_since"] = 0
        write_json_atomic(MONITOR_STATE_FILE, state)

    return json.dumps({"status": "ok", "checked": checked, "alerts": len(fired), "fired": fired})


if __name__ == "__main__":
    mcp.run(transport="stdio")
