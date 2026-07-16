"""Minder Frigate MCP Server — perception sidecar bridge.

Minder configures Frigate from its discovered camera inventory and consumes
Frigate's events/media through these tools. Frigate owns detection, tracking,
zones, and (Phase 4) crop-and-zoom VLM enrichment; Minder stays the brain.

See examples/minder/design/vision-architecture.md.
"""

import base64
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, "/app/mcp-servers")
import contextlib

from _alert_sink import (
    ALERT_COOLDOWN_S,
    execute_device_action,
    load_monitor_state,
    save_monitor_state,
    schedule_active,
    write_alert,
)
from _atomic import write_json_atomic
from _ha import ha_camera_snapshot, ha_states

mcp = FastMCP("minder-frigate")

DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
CAMERAS_FILE = DATA_DIR / "cameras.json"
MEDIA_DIR = DATA_DIR / "frigate"
CAM_USER = os.environ.get("MINDER_CAM_USER", "admin")
FRIGATE_URL = os.environ.get("FRIGATE_URL", "http://localhost:5000").rstrip("/")
RULES_FILE = DATA_DIR / "rules.json"
CURSOR_FILE = DATA_DIR / "frigate_cursor.json"
SHADOW_FILE = DATA_DIR / "poller_shadow.json"
ZONES_FILE = DATA_DIR / "zones.json"  # Scenario Studio zones, keyed by camera name
# "shadow" = log would-be alerts only (parallel-run safety); "live" = fire them.
POLLER_MODE = os.environ.get("MINDER_POLLER_ALERTS", "shadow").lower()
# MQTT event push: Frigate publishes to mosquitto; the webapp subscribes and
# reacts in real time. The minute poller stays as the reconcile backstop.
MQTT_ENABLED = os.environ.get("MINDER_MQTT", "on").lower() in ("on", "1", "true")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
# Post-match VLM enrichment: when an alert fires, describe the snapshot with the
# local VLM and send it as a follow-up — for BOTH Frigate (RTSP) and HA-snapshot
# (cloud-locked) cameras, since Minder has the snapshot for both. Replaces
# Frigate's genai (which only sees RTSP cams and runs on every detection); this
# runs only on the rare rule match. Mirrors Frigate's handling: a generous
# timeout, runs in the background (never blocks the alert), graceful on failure.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
VISION_MODEL = os.environ.get("MINDER_VISION_MODEL", "llava-phi3")
DESCRIBE_ENABLED = os.environ.get("MINDER_DESCRIBE", "on").lower() in ("on", "1", "true")
DESCRIBE_TIMEOUT = int(os.environ.get("MINDER_DESCRIBE_TIMEOUT", "90"))
DESCRIBE_NUM_GPU = int(os.environ.get("MINDER_VISION_NUM_GPU", "0"))
# Describe provider: "ollama" (local VLM, default — keeps the box zero-cloud) or
# "openrouter" (a cloud VLM for higher-fidelity descriptions; opt-in, for testing
# the quality ceiling). The cloud model is MINDER_CLOUD_VISION_MODEL and reuses the
# OpenRouter key the rest of Minder already uses. Cloud failure falls back to local.
DESCRIBE_PROVIDER = os.environ.get("MINDER_DESCRIBE_PROVIDER", "ollama").lower()
CLOUD_VISION_MODEL = os.environ.get("MINDER_CLOUD_VISION_MODEL", "google/gemini-2.5-flash")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_KEY = os.environ.get("MINDER_OPENROUTER_KEY", "") or os.environ.get(
    "OPENROUTER_API_KEY", ""
)
# Recording: needed for event clips (Minder attaches the clip to alerts). Records
# motion segments only (not 24/7) with a short retain, so disk stays bounded.
RECORD_ENABLED = os.environ.get("MINDER_RECORD", "on").lower() in ("on", "1", "true")
RECORD_DAYS = int(os.environ.get("MINDER_RECORD_DAYS", "2"))
# Media-when-ready waits. A live MQTT event fires on "new" — before Frigate has
# written the snapshot, and the clip only exists once the event ENDS. So the text
# alert goes out instantly and the snapshot/clip follow as soon as they exist.
SNAPSHOT_WAIT_S = int(os.environ.get("MINDER_SNAPSHOT_WAIT", "20"))
CLIP_WAIT_S = int(os.environ.get("MINDER_CLIP_WAIT", "90"))
MEDIA_POLL_S = 2.0
# Objects Frigate tracks per camera. Maps onto the same person/vehicle/animal
# vocabulary Minder's scenarios use.
TRACK_OBJECTS = ["person", "car", "dog", "cat"]

# GenAI (crop-and-zoom VLM enrichment): Frigate sends each tracked object's
# snapshot to the local VLM and writes a description onto the event, which the
# poller appends to the alert. Local-only (Ollama); fires per event, never on
# the live stream. Enrichment is additive — a missing description never blocks
# an alert (see _fire / graceful degradation).
GENAI_ENABLED = os.environ.get("MINDER_GENAI", "off").lower() in ("on", "1", "true")
# Describe the security-relevant objects only (keeps VLM load focused on a
# 4GB box); pets are tracked for alerts but not described.
GENAI_OBJECTS = ["person", "car"]
_GENAI_PROMPT = (
    "Analyze the {label} in these security camera images from the {camera}. "
    "Describe concisely and factually what you see."
)
_GENAI_OBJECT_PROMPTS = {
    "person": (
        "Describe the person at the {camera}: appearance, what they are "
        "carrying, and what they are doing. State whether this looks like a "
        "delivery, an expected visitor, or suspicious behaviour."
    ),
    "car": (
        "Describe the vehicle at the {camera}: type and colour, and what it "
        "is doing (arriving, leaving, or parked)."
    ),
}


def _slug(name: str) -> str:
    """Frigate camera keys must be [a-z0-9_]. Deterministic so the event
    poller can map a Frigate event back to the friendly camera name without
    persisting the mapping. 'Main Gate - 2' -> 'main_gate_2'."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _load_cameras() -> list[dict]:
    if not CAMERAS_FILE.exists():
        return []
    try:
        return json.loads(CAMERAS_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        return []


# ---- Scenario Studio zones (drawn regions) ----
# Stored in /data/zones.json keyed by camera NAME (what rules use); points are
# normalized [0..1] (x,y) pairs so the draw canvas is resolution-independent. The
# global Frigate zone key is camera-prefixed because Frigate zone names are global
# in MQTT (frigate/<zone>/<object>) and must be unique across cameras.


def _load_zones() -> dict:
    if not ZONES_FILE.exists():
        return {}
    try:
        data = json.loads(ZONES_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _zone_key(camera: str, zone_name: str) -> str:
    return f"{_slug(camera)}__{_slug(zone_name)}"


def _zone_index() -> dict[str, dict]:
    """Map global zone key -> {camera, name} across all configured zones."""
    idx: dict[str, dict] = {}
    for cam, zones in _load_zones().items():
        for z in zones or []:
            if z.get("name"):
                idx[_zone_key(cam, z["name"])] = {"camera": cam, "name": z["name"]}
    return idx


def _zone_coords(points: list) -> str:
    """Flatten normalized [[x,y],...] to Frigate's 'x1,y1,x2,y2,…' (4 dp)."""
    flat: list[str] = []
    for p in points:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            flat += [f"{float(p[0]):.4f}", f"{float(p[1]):.4f}"]
    return ",".join(flat)


def _frigate_cameras() -> list[dict]:
    """Cameras that belong on the Frigate tier. Prefers the explicit `tier`
    field; falls back to 'has RTSP + ONVIF' for inventories written before
    tiering existed."""
    out = []
    for c in _load_cameras():
        tier = c.get("tier", "")
        if tier == "frigate" or (not tier and c.get("rtsp_url") and c.get("onvif")):
            out.append(c)
    return out


def _substream_url(cam: dict) -> str:
    """Clean Dahua sub-stream URL (subtype=1) for detection. Password is
    templated as {FRIGATE_RTSP_PASSWORD} so no secret lands in the config;
    Frigate substitutes it from the env at load."""
    ip = cam["ip"]
    return (
        f"rtsp://{CAM_USER}:{{FRIGATE_RTSP_PASSWORD}}@{ip}:554/cam/realmonitor?channel=1&subtype=1"
    )


def _build_config(cameras: list[dict]) -> dict:
    """Generate the Frigate config from the frigate-tier cameras. Mirrors the
    validated Phase 0 shape: go2rtc restream + CPU detector + per-camera detect/
    snapshots, whole-frame (no zones — see design Open Question 1)."""
    streams: dict = {}
    cams: dict = {}
    zones_by_cam = _load_zones()
    for cam in cameras:
        key = _slug(cam.get("name") or cam["ip"])
        cam_name = cam.get("name") or cam["ip"]
        streams[key] = [_substream_url(cam)]
        objects: dict = {"track": list(TRACK_OBJECTS)}
        if GENAI_ENABLED:
            # Per-camera enable; prompts come from the global objects.genai
            # defaults below. use_snapshot uses the higher-quality frame.
            objects["genai"] = {
                "enabled": True,
                "use_snapshot": True,
                "objects": list(GENAI_OBJECTS),
            }
        cams[key] = {
            "enabled": True,
            "ffmpeg": {
                "inputs": [
                    {
                        "path": f"rtsp://127.0.0.1:8554/{key}",
                        "input_args": "preset-rtsp-restream",
                        "roles": ["detect"],
                    }
                ]
            },
            "detect": {"enabled": True, "fps": 5},
            # bounding_box draws the detection box (+ label) on the stored event
            # snapshot, which is what alerts attach. Default is True; set it
            # explicitly so the alert image always carries the box.
            "snapshots": {"enabled": True, "bounding_box": True, "retain": {"default": 7}},
            "record": {"enabled": RECORD_ENABLED},
            "objects": objects,
        }
        zdefs = {
            _zone_key(cam_name, z["name"]): {"coordinates": _zone_coords(z["points"])}
            for z in zones_by_cam.get(cam_name, [])
            if z.get("name") and len(z.get("points") or []) >= 3  # a polygon needs >=3 points
        }
        if zdefs:
            cams[key]["zones"] = zdefs
    config: dict = {
        # Publish tracked-object events to mosquitto so Minder reacts in real time
        # (the webapp MQTT subscriber), instead of waiting for the minute poller.
        "mqtt": (
            {"enabled": True, "host": MQTT_HOST, "port": MQTT_PORT}
            if MQTT_ENABLED
            else {"enabled": False}
        ),
        "detectors": {"cpu1": {"type": "cpu"}},
        "go2rtc": {"streams": streams},
        "cameras": cams,
    }
    if RECORD_ENABLED:
        # Frigate 0.17 record retention is per-kind (no top-level/camera `retain`).
        # Keep only event segments — detections + alerts (tracked objects) — for
        # RECORD_DAYS; no `continuous`, so disk stays bounded.
        config["record"] = {
            "enabled": True,
            "detections": {"retain": {"days": RECORD_DAYS}},
            "alerts": {"retain": {"days": RECORD_DAYS}},
        }
    if GENAI_ENABLED:
        config["genai"] = {
            "provider": "ollama",
            "base_url": OLLAMA_URL,
            "model": VISION_MODEL,
        }
        config["objects"] = {
            "genai": {
                "prompt": _GENAI_PROMPT,
                "object_prompts": dict(_GENAI_OBJECT_PROMPTS),
            }
        }
    return config


def _http(
    method: str, path: str, body: bytes | None = None, ctype: str = "application/json"
) -> tuple[int, bytes]:
    url = f"{FRIGATE_URL}{path}"
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def reconfigure_frigate() -> dict:
    """Regenerate Frigate's config (cameras + zones) and apply it (validated save +
    restart). Plain function so the webapp can trigger a reconfigure directly (e.g.
    after a zone is drawn) without going through the MCP tool. Frigate validates the
    config and only applies if valid (400 otherwise), so a bad config can never take
    detection down."""
    cameras = _frigate_cameras()
    if not cameras:
        return {"status": "error", "message": "No frigate-tier cameras"}
    config_yaml = yaml.safe_dump(_build_config(cameras), sort_keys=False)
    status, raw = _http(
        "POST",
        "/api/config/save?save_option=restart",
        body=config_yaml.encode(),
        ctype="text/plain",
    )
    keys = [_slug(c.get("name") or c["ip"]) for c in cameras]
    if status not in (200, 201):
        return {"status": "error", "http": status, "message": raw.decode(errors="replace")[:400]}
    return {"status": "ok", "cameras_configured": len(cameras), "cameras": keys, "reloaded": True}


@mcp.tool()
def configure_cameras() -> str:
    """Generate Frigate's config from the frigate-tier cameras in the inventory
    and apply it (validated save + restart). Run this after camera discovery or
    whenever the camera list changes. Returns the cameras configured."""
    return json.dumps(reconfigure_frigate())


def _slug_to_name() -> dict[str, str]:
    return {_slug(c.get("name") or c["ip"]): (c.get("name") or c["ip"]) for c in _load_cameras()}


def _normalize(ev: dict, slug2name: dict[str, str]) -> dict:
    """Map a Frigate event to Minder's normalized event shape (the contract the
    poller consumes). Media is referenced lazily — only downloaded on alert."""
    cam_slug = ev.get("camera", "")
    data = ev.get("data") or {}
    return {
        "source": "frigate",
        "event_id": ev.get("id", ""),
        "camera": slug2name.get(cam_slug, cam_slug),
        "label": ev.get("label", ""),
        "zone": (ev.get("zones") or [None])[0],
        "ts": ev.get("start_time", 0.0),
        "confidence": ev.get("top_score") or ev.get("score") or data.get("score"),
        "snapshot_ref": f"frigate:{ev.get('id', '')}" if ev.get("has_snapshot") else "",
        "clip_ref": f"frigate:{ev.get('id', '')}" if ev.get("has_clip") else "",
        "description": data.get("description"),
    }


def _fetch_events(
    after: float = 0.0, camera: str = "", label: str = "", limit: int = 50
) -> list[dict]:
    """Fetch + normalize Frigate events. Raises on transport/parse error."""
    params: dict = {"limit": limit, "has_snapshot": 1, "include_thumbnails": 0}
    if after:
        params["after"] = after
    if camera:
        params["cameras"] = _slug(camera)
    if label:
        params["labels"] = label
    status, raw = _http("GET", "/api/events?" + urllib.parse.urlencode(params))
    if status != 200:
        raise RuntimeError(f"frigate events http {status}: {raw[:200]!r}")
    events = json.loads(raw)
    slug2name = _slug_to_name()
    return [_normalize(e, slug2name) for e in events]


@mcp.tool()
def get_events(after: float = 0.0, camera: str = "", label: str = "", limit: int = 50) -> str:
    """Fetch Frigate events since `after` (epoch seconds) as normalized events.
    Empty camera/label means all. Used by the monitoring poller and for
    'what happened' queries."""
    try:
        return json.dumps(_fetch_events(after, camera, label, limit))
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)[:300]})


def _download(path: str, suffix: str, event_id: str) -> str:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    status, raw = _http("GET", path)
    if status != 200 or len(raw) < 1000:
        return ""
    out = MEDIA_DIR / f"{event_id}{suffix}"
    out.write_bytes(raw)
    return str(out)


@mcp.tool()
def get_event_snapshot(event_id: str) -> str:
    """Download a Frigate event's snapshot JPEG to local storage; returns its
    path (empty string if unavailable)."""
    # The box is baked into the stored snapshot via snapshots.bounding_box (set
    # in _build_config) — Frigate 0.17 ignores fetch-time bbox/crop params.
    return json.dumps({"path": _download(f"/api/events/{event_id}/snapshot.jpg", ".jpg", event_id)})


@mcp.tool()
def get_event_clip(event_id: str) -> str:
    """Download a Frigate event's clip MP4 to local storage; returns its path
    (empty string if unavailable)."""
    return json.dumps({"path": _download(f"/api/events/{event_id}/clip.mp4", ".mp4", event_id)})


# ---- HA snapshot tier (cloud-locked cameras via Home Assistant) ----
#
# Cameras with no local RTSP (e.g. Xiaomi via Mi Home) are surfaced through
# Home Assistant: the camera's own on-device motion/person detection appears as
# a binary_sensor, and Minder reads it into the SAME event stream as Frigate.
# Lower fidelity (no tracking/zones, cloud-dependent) — the secondary tier.


def _ha_cameras() -> list[dict]:
    return [c for c in _load_cameras() if c.get("tier") == "ha-snapshot" and c.get("ha_entity")]


def _parse_ha_time(s: str) -> float:
    try:
        import datetime

        return datetime.datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0.0


@mcp.tool()
def register_ha_camera(name: str, motion_entity: str, camera_entity: str = "") -> str:
    """Register a cloud-locked camera (no RTSP) on the HA snapshot tier.
    motion_entity is its HA motion/person binary_sensor (what we watch);
    camera_entity is its HA camera entity (for snapshots, optional)."""
    cameras = _load_cameras()
    for c in cameras:
        if c.get("ha_entity") == motion_entity or c.get("name") == name:
            c.update(
                {
                    "name": name,
                    "tier": "ha-snapshot",
                    "ha_entity": motion_entity,
                    "snapshot_url": camera_entity,
                }
            )
            _save_cameras(cameras)
            return json.dumps({"status": "ok", "updated": name})
    cameras.append(
        {
            "ip": "",
            "manufacturer": "ha",
            "model": "cloud-camera",
            "firmware": "",
            "serial": "",
            "rtsp_url": "",
            "snapshot_url": camera_entity,
            "name": name,
            "onvif": False,
            "osd_name": "",
            "tier": "ha-snapshot",
            "ha_entity": motion_entity,
        }
    )
    _save_cameras(cameras)
    return json.dumps({"status": "ok", "registered": name})


def _save_cameras(cameras: list[dict]) -> None:
    write_json_atomic(CAMERAS_FILE, cameras)


def _fetch_ha_events(after: float = 0.0) -> list[dict]:
    """Read HA-tier cameras' motion/person sensors into normalized events.
    A motion/occupancy 'on' since `after` becomes a person event (motion is the
    'someone is there' signal for the secondary tier)."""
    cams = _ha_cameras()
    if not cams:
        return []
    states = {s.get("entity_id"): s for s in ha_states()}
    out = []
    for c in cams:
        s = states.get(c["ha_entity"])
        if not s or s.get("state") != "on":
            continue
        ts = _parse_ha_time(s.get("last_changed", ""))
        if ts <= after:
            continue
        out.append(
            {
                "source": "ha",
                "event_id": f"ha:{c['ha_entity']}:{int(ts)}",
                "camera": c.get("name") or c["ha_entity"],
                "label": "person",
                "zone": None,
                "ts": ts,
                "confidence": None,
                "snapshot_ref": f"ha:{c['snapshot_url']}" if c.get("snapshot_url") else "",
                "clip_ref": "",
                "description": None,
            }
        )
    return out


# ---- Monitoring poller (replaces the deterministic YOLO loop) ----

_LABEL_WORDS = {
    "person": {
        "person",
        "people",
        "someone",
        "somebody",
        "anyone",
        "man",
        "woman",
        "intruder",
        "stranger",
        "burglar",
        "human",
    },
    "car": {"car", "vehicle", "truck", "van", "auto", "automobile"},
    "dog": {"dog", "puppy"},
    "cat": {"cat", "kitten"},
}


def _condition_to_labels(condition: str) -> set[str]:
    """Map a rule's free-text condition to Frigate object labels. Conditions
    with no object (e.g. 'is the gate open') return empty — those stay on the
    Minder-managed path, not the event stream."""
    words = set(re.findall(r"[a-z]+", (condition or "").lower()))
    if "animal" in words or "pet" in words:
        return {"dog", "cat"}
    labels = {lbl for lbl, syns in _LABEL_WORDS.items() if words & syns}
    return labels


def _rule_cameras(rule: dict) -> list[str]:
    """Cameras a rule watches, as a list. Backward-compatible with rules that
    stored a single ``camera`` string before multi-camera support."""
    cams = rule.get("cameras")
    if isinstance(cams, list) and cams:
        return cams
    return [rule.get("camera", "all")]


def _one_camera_match(rule_camera: str, event_camera: str) -> bool:
    rc = (rule_camera or "all").lower().strip()
    if rc in ("", "all", "any"):
        return True
    ec = (event_camera or "").lower()
    if rc in ec or ec in rc:
        return True
    rc_w = set(re.findall(r"[a-z0-9]+", rc))
    ec_w = set(re.findall(r"[a-z0-9]+", ec))
    return bool(rc_w & ec_w)


def _camera_match(cameras: list[str], event_camera: str) -> bool:
    """True if the event's camera matches any of the rule's cameras."""
    return any(_one_camera_match(rc, event_camera) for rc in (cameras or ["all"]))


def _record_shadow(entry: dict) -> None:
    rows = []
    if SHADOW_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            rows = json.loads(SHADOW_FILE.read_text())
    rows.append(entry)
    SHADOW_FILE.write_text(json.dumps(rows[-200:], indent=2))


# What YOLO actually detected, in words the VLM should describe. Grounding the
# prompt in the detected object stops a small VLM inventing entities that aren't
# there (it described "a person in a red shirt" on a car-only detection).
_SUBJECT = {"car": "vehicle", "person": "person", "dog": "animal", "cat": "animal"}


def _describe_prompt(label: str, cam: str = "", condition: str = "") -> str:
    """A grounded, conservative instruction. The snapshot is the low-res detect
    stream (~352x288), so a VLM asked open-endedly fabricates confident specifics
    — it invents people/counts and "reads" licence plates it cannot resolve. The
    CPU benchmark (scripts/vlm_bench.py) confirmed: the open prompt reproduces the
    hallucination on every model; this grounded one removes it. So: anchor to what
    YOLO detected (and where, and which watch-rule fired — the alert's cause, which
    orients the model), forbid the fabrication-prone moves, ask for ONE sentence."""
    subject = _SUBJECT.get(label, "")
    where = f" on the {cam} camera" if cam else ""
    lead = (
        f"A home security camera{where} detected a {subject}. Describe only that {subject}"
        if subject
        else f"Describe only what is clearly visible in this security camera image{where}"
    )
    cause = f' The camera is watching for: "{condition}".' if condition else ""
    return (
        f"You are a home security assistant.{cause} {lead}, in ONE short, factual "
        "sentence (plain prose, no preamble, no list). State only what is clearly "
        "visible — colour, type, and rough position. Do NOT read, guess, or mention "
        "any licence plate, text, sign, or number. Do NOT mention any person, animal, "
        "or vehicle that is not clearly visible, and do NOT guess how many there are. "
        "Describe only what you actually see — do not simply restate the watch "
        "condition. If a detail is unclear, leave it out rather than guess. Ignore any "
        "timestamp overlay."
    )


def _log(msg: str) -> None:
    """Diagnostic line to stderr (stdout is the MCP protocol). Makes the describe/
    escalate provider path visible — so a silent cloud→local fallback (wrong-colour
    local answers) is diagnosable instead of a mystery."""
    print(f"[minder.frigate] {msg}", file=sys.stderr, flush=True)


def _cloud_vlm(img_b64: str, prompt: str, model: str = "") -> str:
    """One cloud VLM call (OpenRouter, OpenAI-compatible vision chat). Empty string
    without a key or on ANY error — the caller falls back to local. `model` overrides
    the default MINDER_CLOUD_VISION_MODEL (per-rule escalate override)."""
    if not OPENROUTER_KEY:
        return ""
    payload = json.dumps(
        {
            "model": model or CLOUD_VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 200,
        }
    ).encode()
    try:
        req = urllib.request.Request(
            f"{OPENROUTER_URL}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
        )
        r = json.loads(urllib.request.urlopen(req, timeout=DESCRIBE_TIMEOUT).read())
        return ((r.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
    except Exception as e:
        _log(f"cloud VLM failed ({model or CLOUD_VISION_MODEL}): {e}")
        return ""  # graceful — caller falls back to the local VLM


# Back-compat alias (older callers / tests): the fixed-model cloud describe.
def _describe_via_cloud(img_b64: str, prompt: str) -> str:
    return _cloud_vlm(img_b64, prompt)


def _local_vlm(img_b64: str, prompt: str, num_predict: int = 80) -> str:
    """One local VLM call (Ollama). Low temperature for grounded fact. Empty on error."""
    payload = json.dumps(
        {
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
            "stream": False,
            "think": False,
            "keep_alive": -1,
            "options": {
                "temperature": 0.1,
                "num_gpu": DESCRIBE_NUM_GPU,
                "num_predict": num_predict,
            },
        }
    ).encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat", data=payload, headers={"Content-Type": "application/json"}
        )
        r = json.loads(urllib.request.urlopen(req, timeout=DESCRIBE_TIMEOUT).read())
        return (r.get("message", {}).get("content") or "").strip()
    except Exception as e:
        _log(f"local VLM failed: {e}")
        return ""


def _describe_snapshot(path: str, label: str = "", cam: str = "", condition: str = "") -> str:
    """Describe an alert snapshot, grounded in the object YOLO detected (`label`) and
    the alert's cause (`cam` + watch `condition`). Uses the cloud VLM when
    MINDER_DESCRIBE_PROVIDER=openrouter (falling back to local on failure), else the
    local VLM (Ollama). Graceful empty string on ANY failure (the alert already fired
    — the description is additive). Runs on CPU by default."""
    try:
        img = base64.b64encode(Path(path).read_bytes()).decode()
    except Exception:
        return ""
    prompt = _describe_prompt(label, cam, condition)
    if DESCRIBE_PROVIDER == "openrouter" and OPENROUTER_KEY:
        cloud = _cloud_vlm(img, prompt)
        if cloud:
            _log(f"describe: cloud VLM ({CLOUD_VISION_MODEL})")
            return cloud
        _log("describe: cloud unavailable → local VLM fallback")
    out = _local_vlm(img, prompt)
    if out:
        _log(f"describe: local VLM ({VISION_MODEL})")
    return out


def _await_media(fetch: Callable[[], str], timeout_s: float) -> str:
    """Poll a media fetch (returns a JSON ``{"path": ...}`` string, "" path until
    Frigate has written the file) until it yields a path or the timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while True:
        with contextlib.suppress(Exception):
            path = json.loads(fetch()).get("path", "")
            if path:
                return path
        if time.monotonic() >= deadline:
            return ""
        time.sleep(MEDIA_POLL_S)


def _deliver_event_media(event_id: str, cam: str, label: str = "", condition: str = "") -> None:
    """Send a Frigate event's media as follow-ups once it exists. The text alert
    already went out; a live MQTT "new" event fires before the snapshot is written
    and the clip only exists after the event ends, so we wait here (in a daemon
    thread, never blocking the alert path) for each and send it when ready — the
    boxed snapshot + VLM description (grounded in `label`, the object YOLO detected,
    plus the alert's cause: `cam` + watch `condition`), then the recorded clip.
    Graceful if either never materialises (snapshot off / recording off / VLM
    slow or down)."""

    def _run() -> None:
        snapshot_path = _await_media(lambda: get_event_snapshot(event_id), SNAPSHOT_WAIT_S)
        if snapshot_path:
            # Photo only — the text alert already named the event (empty message
            # so the bot skips a duplicate 🚨 bubble). The box is baked in.
            write_alert("", cam, snapshot_path)
            if DESCRIBE_ENABLED:
                desc = _describe_snapshot(snapshot_path, label, cam, condition)
                if desc:
                    write_alert(f"📷 {cam}: {desc}", cam)
        if RECORD_ENABLED:
            video_path = _await_media(lambda: get_event_clip(event_id), CLIP_WAIT_S)
            if video_path:
                write_alert("", cam, "", video_path)

    threading.Thread(target=_run, daemon=True).start()


def _deliver_ha_media(ev: dict, cam: str, condition: str = "") -> None:
    """HA-snapshot tier: HA pulls a live frame synchronously (no wait) and there
    is no clip. Sent in a daemon thread so the VLM describe never blocks."""
    ref = ev.get("snapshot_ref") or ""
    if not ref:
        return

    def _run() -> None:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        out = MEDIA_DIR / (ev["event_id"].replace(":", "_") + ".jpg")
        snapshot_path = ha_camera_snapshot(ref.split("ha:", 1)[1], str(out))
        if not snapshot_path:
            return
        write_alert("", cam, snapshot_path)
        if DESCRIBE_ENABLED:
            desc = _describe_snapshot(snapshot_path, ev.get("label", ""), cam, condition)
            if desc:
                write_alert(f"📷 {cam}: {desc}", cam)

    threading.Thread(target=_run, daemon=True).start()


# ---- Severity + VLM escalation (design/scenario-studio-severity-escalation.md) ----
# A rule may carry `severity` (info|warning|critical) + an `escalate` block. When
# present, the deterministic match is only a PRE-FILTER: a VLM answers a grounded
# yes/no on the alert snapshot, and the alert + actions fire only on a matching
# answer. Cloud tier for critical (auto), local otherwise; cloud failure falls back
# to local. A verdict we trust wins; when verification is impossible a *critical* rule
# still fires (flagged "unverified") so we never miss it. `cooldown_s` rate-limits.

_YES_RE = re.compile(r"^\W*(yes|yeah|yep|yup|true|affirmative)\b", re.I)
_SEVERITY_TAG = {"critical": "🚨 CRITICAL", "warning": "⚠️", "info": "🔵"}


def _is_yes(answer: str) -> bool:
    """A confirmation counts as yes only when the answer STARTS with yes (we prompt
    for 'yes/no first'), so 'No, just a neighbour' isn't misread as a match."""
    return bool(_YES_RE.match((answer or "").strip()))


def _escalate_tier(rule: dict) -> str:
    """The VLM tier for a rule's escalate block: explicit `tier` wins; `auto`/absent
    maps from severity — critical → cloud, else local."""
    tier = ((rule.get("escalate") or {}).get("tier") or "auto").lower()
    if tier in ("cloud", "local"):
        return tier
    return "cloud" if (rule.get("severity") or "").lower() == "critical" else "local"


def _vlm_confirm(img_b64: str, question: str, tier: str, model: str = "") -> tuple[str, str]:
    """Ask the VLM a grounded yes/no about the snapshot. Returns (answer, provider)
    where provider is "cloud" | "local" | "" (all failed). tier=cloud tries cloud then
    falls back to local; tier=local goes straight to Ollama."""
    prompt = (
        "You are a home-security camera assistant. Answer with 'yes' or 'no' as the "
        "FIRST word, then a short reason based only on what you actually see. "
        f"Question: {question}"
    )
    if tier == "cloud":
        ans = _cloud_vlm(img_b64, prompt, model)
        if ans:
            return ans, "cloud"
        _log("escalate: cloud VLM unavailable → local fallback")
    ans = _local_vlm(img_b64, prompt, num_predict=60)
    return (ans, "local") if ans else ("", "")


def _escalate_snapshot(ev: dict) -> str:
    """Fetch the snapshot for an escalate decision — a Frigate event snapshot or an
    HA live frame — reusing the normal media waits. "" if none is available."""
    if ev.get("source") == "frigate" and ev.get("event_id"):
        return _await_media(lambda: get_event_snapshot(ev["event_id"]), SNAPSHOT_WAIT_S)
    ref = ev.get("snapshot_ref") or ""
    if ev.get("source") == "ha" and ref.startswith("ha:"):
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        out = MEDIA_DIR / (str(ev.get("event_id", "esc")).replace(":", "_") + ".jpg")
        return ha_camera_snapshot(ref.split("ha:", 1)[1], str(out))
    return ""


def _run_escalated_actions(rule: dict, cam: str) -> None:
    """Fire a confirmed escalate rule's non-alert actions: device actions and
    `notify` (an urgent alert naming the contact — the channel adapters fan it out)."""
    for act in rule.get("actions") or []:
        t = act.get("type")
        if t == "device":
            try:
                write_alert(execute_device_action(act["device"], act["action"]), cam)
            except Exception as e:
                write_alert(f"device {act.get('device')} failed: {e}", cam)
        elif t == "notify":
            who = act.get("contact") or act.get("to") or "contact"
            write_alert(f"🔔 Escalation: notifying {who} — {cam}", cam)


def _deliver_escalated(rule: dict, ev: dict, now: float) -> None:
    """Escalate path: gate the alert on a VLM confirmation. In a daemon thread (never
    blocks the poller): rate-limit, fetch the snapshot, ask the VLM, and fire the alert
    + actions only on a matching answer. A trusted 'no' suppresses the alert (the whole
    point); when verification is impossible a *critical* rule fires 'unverified'."""
    esc = rule.get("escalate") or {}
    cam = ev["camera"]
    severity = (rule.get("severity") or "warning").lower()
    cooldown = int(esc.get("cooldown_s", ALERT_COOLDOWN_S))
    require = (esc.get("require") or "yes").lower()

    def _run() -> None:
        state = load_monitor_state()
        key = f"escalate|{cam}|{rule.get('condition')}"
        if now - state.get(key, 0) < cooldown:
            return
        snapshot = _escalate_snapshot(ev)
        answer, provider = "", ""
        if snapshot:
            try:
                img = base64.b64encode(Path(snapshot).read_bytes()).decode()
                answer, provider = _vlm_confirm(
                    img, esc.get("prompt", ""), _escalate_tier(rule), esc.get("model", "")
                )
            except Exception as e:
                _log(f"escalate: confirm error on {cam}: {e}")
        confirmed = _is_yes(answer) if require == "yes" else (require in answer.lower())
        got_verdict = bool(snapshot and answer)

        if got_verdict and not confirmed:
            _log(f"escalate near-miss {cam} ({rule.get('condition')}): {answer[:70]!r}")
            state[key] = now
            save_monitor_state(state)
            return
        unconfirmed = False
        if not got_verdict:
            if severity != "critical":
                _log(f"escalate: unverifiable {cam} ({rule.get('condition')}), non-critical → drop")
                return
            unconfirmed = True  # critical + unverifiable → alert anyway, flagged

        state[key] = now
        save_monitor_state(state)
        tag = _SEVERITY_TAG.get(severity, "⚠️")
        note = f" — {answer}" if answer else ""
        flag = " (unverified)" if unconfirmed else ""
        base = rule.get("condition") or ev.get("label") or "alert"
        _log(f"escalate FIRE {cam} [{severity}] via {provider or 'none'}")
        write_alert(f"{tag} {base} — {cam}{note}{flag}", cam, snapshot or "")
        _run_escalated_actions(rule, cam)
        if RECORD_ENABLED and ev.get("source") == "frigate" and ev.get("event_id"):
            video = _await_media(lambda: get_event_clip(ev["event_id"]), CLIP_WAIT_S)
            if video:
                write_alert("", cam, "", video)

    threading.Thread(target=_run, daemon=True).start()


def _fire(rule: dict, ev: dict, now: float, live: bool) -> str:
    """Fire a matched rule — live writes the real alert + runs device actions;
    shadow only logs the would-be alert for parallel-run comparison.

    The text alert fires instantly; the snapshot and clip follow as soon as
    Frigate has written them (see _deliver_event_media) — a live MQTT event has
    neither ready at fire time, so attaching them inline would drop them."""
    actions = rule.get("actions") or [{"type": "alert"}]
    cam = ev["camera"]
    desc = ev.get("description")
    msg = f"{rule.get('condition') or ev['label']} — {cam}"
    if desc:
        msg += f": {desc}"
    if not live:
        _record_shadow(
            {
                "ts": now,
                "camera": cam,
                "label": ev["label"],
                "condition": rule.get("condition"),
                "would_alert": msg,
            }
        )
        return "shadow"
    # Escalate rules don't fire instantly — the VLM confirmation is the gate. Route the
    # whole decision (snapshot → VLM yes/no → alert + actions) to the escalated path.
    if rule.get("escalate"):
        _deliver_escalated(rule, ev, now)
        return "escalate"
    notes = []
    for act in actions:
        if act.get("type") == "device":
            try:
                notes.append(execute_device_action(act["device"], act["action"]))
            except Exception as e:
                notes.append(f"device {act.get('device')} failed: {e}")
    if any(a.get("type") == "alert" for a in actions) or notes:
        if notes:
            msg += "\n" + "\n".join(notes)
        write_alert(msg, cam)  # instant text — media follows when ready
        cond = rule.get("condition") or ""
        if ev.get("source") == "frigate" and ev.get("event_id"):
            _deliver_event_media(ev["event_id"], cam, ev.get("label", ""), cond)
        elif ev.get("source") == "ha":
            _deliver_ha_media(ev, cam, cond)
    return "live"


def _run_time_rules(active: list[dict], state: dict, now: float, live: bool) -> int:
    """Fire daily time rules (at_time set, no event needed). Folded into the
    same per-minute tick so the cron has a single consumer."""
    fired = 0
    now_local = time.localtime(now)
    for rule in active:
        at_time = rule.get("at_time") or ""
        if not at_time:
            continue
        if rule.get("target") == "ha":
            continue  # compiled to a native HA automation — HA fires it
        try:
            h, m = map(int, at_time.split(":"))
        except ValueError:
            continue
        delta = (now_local.tm_hour * 60 + now_local.tm_min) - (h * 60 + m)
        if not (0 <= delta < 5):
            continue
        key = f"time|{at_time}|{json.dumps(rule.get('actions', []))[:100]}"
        if now - state.get(key, 0) < 20 * 3600:
            continue
        state[key] = now
        notes = []
        for act in rule.get("actions", []):
            if act.get("type") == "device" and live:
                try:
                    notes.append(execute_device_action(act["device"], act["action"]))
                except Exception as e:
                    notes.append(f"device {act.get('device')} failed: {e}")
        msg = f"Scheduled ({at_time}): " + ("; ".join(notes) or "alert")
        if live:
            write_alert(msg, "")
        else:
            _record_shadow(
                {"ts": now, "camera": "", "condition": f"@{at_time}", "would_alert": msg}
            )
        fired += 1
    return fired


def _sensor_rule_matches(rule: dict, state_val: str) -> bool:
    """True if an HA sensor's current value satisfies the rule's trigger — a
    numeric threshold (trigger_op/trigger_threshold) or a state match
    (trigger_state, default 'on')."""
    op = rule.get("trigger_op")
    thr = rule.get("trigger_threshold")
    if op and thr is not None:
        try:
            v = float(state_val)
        except (TypeError, ValueError):
            return False
        return {">=": v >= thr, "<=": v <= thr, ">": v > thr, "<": v < thr}.get(op, False)
    return str(state_val).lower() == str(rule.get("trigger_state") or "on").lower()


def _run_sensor_rules(active: list[dict], state: dict, now: float, live: bool) -> int:
    """Fire sensor-triggered rules (trigger_entity set) by reading HA state. Edge-
    triggered: fires on the transition INTO the matching state, not every cycle it
    stays matched, plus the standard cooldown. Folded into the same per-minute
    tick as the Frigate poll + time rules."""
    sensor_rules = [r for r in active if r.get("trigger_entity")]
    if not sensor_rules:
        return 0
    try:
        states = {s.get("entity_id"): s for s in ha_states()}
    except Exception:
        return 0
    matched = state.setdefault("_sensor_matched", {})
    fired = 0
    for rule in sensor_rules:
        eid = rule["trigger_entity"]
        s = states.get(eid)
        if not s:
            continue
        val = s.get("state")
        if val in (None, "unavailable", "unknown"):
            continue
        key = f"sensor|{eid}|{json.dumps(rule.get('actions', []))[:80]}"
        now_match = _sensor_rule_matches(rule, val)
        was_match = matched.get(key, False)
        matched[key] = now_match
        if not now_match or was_match:
            continue  # only the rising edge (not-matched -> matched) fires
        if now - state.get(key, 0) < ALERT_COOLDOWN_S:
            continue
        state[key] = now
        notes = []
        for act in rule.get("actions", []):
            if act.get("type") == "device" and live:
                try:
                    notes.append(execute_device_action(act["device"], act["action"]))
                except Exception as e:
                    notes.append(f"device {act.get('device')} failed: {e}")
        label = rule.get("trigger_sensor") or eid
        msg = f"🔔 {label}: {val}" + (("\n" + "; ".join(notes)) if notes else "")
        if live:
            write_alert(msg, "")
        else:
            _record_shadow(
                {"ts": now, "camera": "", "condition": f"sensor:{eid}", "would_alert": msg}
            )
        fired += 1
    return fired


def _active_rules(rules: list[dict]) -> list[dict]:
    return [
        r
        for r in rules
        if r.get("enabled", True)
        and r.get("target") != "ha"
        and schedule_active(r.get("schedule", "always"))
    ]


def _match_and_fire_event(
    ev: dict, active: list[dict], state: dict, now: float, live: bool
) -> dict | None:
    """Match ONE normalized event against the active rules and fire the best match.
    An **escalate** (AI-verify) rule takes precedence over a plain rule for the same
    event, so a broad "person" rule can't shadow a specific escalate rule (e.g. a
    per-camera "person → is it a weapon?" gate). Among same-priority matches the first
    in file order wins. Mutates `state` (cooldown). Shared by the cron poller and the
    MQTT subscriber so they never double-fire."""
    plain: dict | None = None
    escalated: dict | None = None
    for rule in active:
        if rule.get("at_time"):
            continue  # time rules handled separately
        if rule.get("condition_type") in ("count", "cross"):
            continue  # count/cross fire from their own paths, not per-event presence
        if not _camera_match(_rule_cameras(rule), ev["camera"]):
            continue
        rule_zone = (rule.get("zone") or "").strip()
        if rule_zone and (ev.get("zone") or "") != _zone_key(ev["camera"], rule_zone):
            continue  # zone presence rule: the event must be inside that zone
        labels = _condition_to_labels(rule.get("condition", ""))
        if not labels or ev["label"] not in labels:
            continue  # non-object condition or wrong object → not this rule
        if rule.get("escalate"):
            escalated = escalated or rule  # first escalate match wins (highest priority)
        else:
            plain = plain or rule
    rule = escalated or plain
    if rule is None:
        return None
    # Cooldown is per (camera, condition) — the chosen rule's own key. If it's cooling
    # down we suppress this event rather than fall back to a lesser rule (no double alert).
    key = f"{ev['camera']}|{rule.get('condition')}"
    if now - state.get(key, 0) < ALERT_COOLDOWN_S:
        return None
    state[key] = now
    return {"camera": ev["camera"], "label": ev["label"], "mode": _fire(rule, ev, now, live)}


# ---- Scenario Studio Phase 1: count conditions ----
# A count rule fires when count(object on a camera) {op} value, held for debounce_s.
# Source is Frigate's retained object-count MQTT topics (frigate/<camera>/<object>),
# evaluated deterministically here — no LLM at match time (design scenario-studio.md
# §"Condition grammar"). Whole-frame per-camera counts for now; per-zone counts
# (frigate/<zone>/<object>) slot into the same path once zones are configured.

_COUNT_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda n, v: n > v,
    ">=": lambda n, v: n >= v,
    "<": lambda n, v: n < v,
    "<=": lambda n, v: n <= v,
    "==": lambda n, v: n == v,
    "=": lambda n, v: n == v,
}
DEFAULT_COUNT_DEBOUNCE_S = 3.0


def _count_object_label(obj: str) -> str:
    """Normalize a rule's count object (or an MQTT topic segment) to a canonical
    Frigate label using the same synonym table presence uses (vehicle->car, …)."""
    w = {(obj or "").lower().strip()}
    if "animal" in w or "pet" in w:
        return "dog"  # animal group maps to dog/cat; counts compare per-label
    for label, syns in _LABEL_WORDS.items():
        if w & syns or (obj or "").lower().strip() == label:
            return label
    return (obj or "").lower().strip()


def _count_compares(op: str, count: float, value: float) -> bool:
    fn = _COUNT_OPS.get((op or ">").strip())
    return bool(fn and fn(count, value))


def _eval_count_rule(rule: dict, count: int, now: float, state: dict) -> bool:
    """Decide whether a count rule should fire NOW, mutating its debounce/cooldown
    state. Fires once when the condition has held for debounce_s, re-arms only after
    the condition goes false again, and never re-fires within ALERT_COOLDOWN_S."""
    op = rule.get("count_op", ">")
    value = rule.get("count_value", 0)
    debounce = float(rule.get("debounce_s", DEFAULT_COUNT_DEBOUNCE_S))
    cstate = state.setdefault("_count", {})
    key = f"count|{_rule_cameras(rule)}|{rule.get('count_object')}|{op}|{value}"
    st = cstate.setdefault(key, {"true_since": None, "last_fired": 0.0})

    if not _count_compares(op, count, value):
        st["true_since"] = None  # condition cleared -> re-arm
        return False
    if st["true_since"] is None:
        st["true_since"] = now
    held = now - st["true_since"]
    if held < debounce:
        return False
    last = st.get("last_fired", 0.0)
    if last and now - last < ALERT_COOLDOWN_S:
        return False
    st["last_fired"] = now
    return True


def handle_count_update(source_key: str, obj: str, count: int) -> dict | None:
    """Evaluate active count rules against one Frigate object-count update
    (camera/zone slug + object label + current count). Fires matching rules.
    Shared, testable core called by the MQTT count subscriber. Returns fired info
    or None."""
    if not RULES_FILE.exists():
        return None
    try:
        rules = json.loads(RULES_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        return None
    active = [r for r in _active_rules(rules) if r.get("condition_type") == "count"]
    if not active:
        return None
    # source_key is either a zone key (frigate/<zone>/<obj>) or a camera slug
    # (frigate/<camera>/<obj>). A zone key resolves to (camera, zone); otherwise it's
    # a whole-frame camera-level count.
    zinfo = _zone_index().get(source_key)
    update_zone = zinfo["name"] if zinfo else None
    cam_name = zinfo["camera"] if zinfo else _slug_to_name().get(source_key, source_key)
    obj_label = _count_object_label(obj)
    now = time.time()
    live = POLLER_MODE == "live"
    state = load_monitor_state()
    fired = None
    for rule in active:
        if _count_object_label(rule.get("count_object", "")) != obj_label:
            continue
        rule_zone = (rule.get("zone") or "").strip()
        if rule_zone:
            # Zone rule: fire only on its own zone's count topic, on a matching camera.
            if update_zone is None or _slug(update_zone) != _slug(rule_zone):
                continue
        elif update_zone is not None:
            continue  # whole-frame rule ignores zone topics (camera topic is its source)
        if not _camera_match(_rule_cameras(rule), cam_name):
            continue
        if _eval_count_rule(rule, count, now, state):
            where = f" in {update_zone}" if update_zone else ""
            ev = {
                "camera": cam_name,
                "label": f"{count} {obj_label}",
                "source": "count",
                "description": f"{count} {obj_label}{where} (limit {rule.get('count_op', '>')}"
                f"{rule.get('count_value')})",
            }
            fired = {
                "camera": cam_name,
                "zone": update_zone,
                "count": count,
                "mode": _fire(rule, ev, now, live),
            }
            break
    save_monitor_state(state)
    return fired


# ---- Scenario Studio: cross conditions (zone enter / leave) ----
# A line crossing is modelled as a tracked object ENTERING or LEAVING a (thin) zone
# drawn over the boundary — "forklift crosses into the pedestrian lane" = forklift
# enters that zone. Directional (enter vs leave), which is what distinguishes it from
# presence ("is currently in"). Detected from the per-object zone-membership
# transitions in Frigate's live event stream — deterministic, no LLM. Real-time
# (MQTT) only: the per-minute REST poll can't see transitions.

_CROSS_STATE_TTL_S = 600.0  # forget a tracked object's zone membership after 10 min


def _cross_direction_match(direction: str, entered: list, left: list, zkey: str) -> bool:
    d = (direction or "enter").lower()
    if d in ("leave", "exit", "out"):
        return zkey in left
    if d in ("any", "both", "cross"):
        return zkey in entered or zkey in left
    return zkey in entered  # default: enter / in


def handle_cross_event(after: dict, active: list[dict], state: dict, now: float, live: bool):
    """Detect zone enter/leave for one tracked object (one Frigate event 'after') and
    fire matching cross rules. Tracks per-object zone membership across updates, so it
    runs on EVERY event (before the presence dedup). Returns fired info or None."""
    cross_rules = [r for r in active if r.get("condition_type") == "cross"]
    if not cross_rules:
        return None
    event_id = after.get("id", "")
    current = list(after.get("current_zones") or [])
    cstate = state.setdefault("_cross_zones", {})
    prev = (cstate.get(event_id) or {}).get("zones", [])
    entered = [z for z in current if z not in prev]
    left = [z for z in prev if z not in current]
    cstate[event_id] = {"zones": current, "ts": now}
    for k in [k for k, v in cstate.items() if now - v.get("ts", now) > _CROSS_STATE_TTL_S]:
        cstate.pop(k, None)
    if not entered and not left:
        return None
    cam_name = _slug_to_name().get(after.get("camera", ""), after.get("camera", ""))
    obj_label = _count_object_label(after.get("label", ""))
    for rule in cross_rules:
        want = (rule.get("object") or "").strip()
        if want and _count_object_label(want) != obj_label:
            continue
        if not _camera_match(_rule_cameras(rule), cam_name):
            continue
        rzone = (rule.get("zone") or "").strip()
        if not rzone:
            continue
        zkey = _zone_key(cam_name, rzone)
        if not _cross_direction_match(rule.get("direction"), entered, left, zkey):
            continue
        key = f"cross|{cam_name}|{zkey}|{rule.get('direction', 'enter')}"
        if now - state.get(key, 0) < ALERT_COOLDOWN_S:
            continue
        state[key] = now
        d = (rule.get("direction") or "enter").lower()
        verb = "left" if d in ("leave", "exit", "out") else "entered"
        ev = {
            "camera": cam_name,
            "label": f"{obj_label} {verb} {rzone}",
            "source": "cross",
            "description": f"{obj_label or 'object'} {verb} {rzone}",
        }
        return {
            "camera": cam_name,
            "zone": rzone,
            "direction": d,
            "mode": _fire(rule, ev, now, live),
        }
    return None


def _normalize_mqtt(after: dict, slug2name: dict[str, str]) -> dict:
    """Frigate's MQTT 'after' object → the normalized event shape (_normalize
    expects REST field names; MQTT uses current_zones instead of zones)."""
    a = dict(after)
    if "zones" not in a and "current_zones" in a:
        a["zones"] = a.get("current_zones")
    return _normalize(a, slug2name)


def handle_live_event(after: dict) -> dict | None:
    """Process one Frigate MQTT event (the 'after' object): load active rules +
    shared state, dedupe by event id, match + fire. Returns the fired info or
    None. Called by the webapp's MQTT subscriber for real-time alerting."""
    if not after.get("id") or not after.get("label"):
        return None
    if not RULES_FILE.exists():
        return None
    try:
        rules = json.loads(RULES_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        return None
    now = time.time()
    live = POLLER_MODE == "live"
    active = _active_rules(rules)
    if not active:
        return None
    ev = _normalize_mqtt(after, _slug_to_name())
    state = load_monitor_state()
    # Cross runs on EVERY event (it tracks zone-membership transitions); presence is
    # deduped to fire once per object.
    cross_fired = handle_cross_event(after, active, state, now, live)
    presence_fired = None
    seen = set(state.get("_frigate_seen", []))
    if ev["event_id"] not in seen:
        seen.add(ev["event_id"])
        presence_fired = _match_and_fire_event(ev, active, state, now, live)
        state["_frigate_seen"] = list(seen)[-500:]
    save_monitor_state(state)
    return cross_fired or presence_fired


@mcp.tool()
def poll_events() -> str:
    """Poll Frigate for new events and apply the monitoring scenarios — the
    event-driven replacement for the YOLO snapshot loop. Reads rules from
    rules.json, matches each new tracked event by camera + object label +
    schedule, dedupes per event, and fires alerts/device actions (or logs them
    in shadow mode). Deterministic — call once per monitoring cycle."""
    live = POLLER_MODE == "live"
    if not RULES_FILE.exists():
        return json.dumps({"status": "idle", "reason": "no rules", "mode": POLLER_MODE})
    try:
        rules = json.loads(RULES_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"status": "idle", "reason": "rules unreadable"})
    # target=="ha" rules are compiled to native HA automations — HA fires them.
    active = _active_rules(rules)

    now = time.time()
    cursor = now - 120.0
    if CURSOR_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            cursor = json.loads(CURSOR_FILE.read_text()).get("after", cursor)
    try:
        events = _fetch_events(after=cursor, limit=100)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)[:200]})
    # Fold in the HA snapshot tier (cloud-locked cameras) — same event stream.
    with contextlib.suppress(Exception):
        events = events + _fetch_ha_events(cursor)

    state = load_monitor_state()
    seen = set(state.get("_frigate_seen", []))
    fired = []
    max_ts = cursor
    for ev in events:
        max_ts = max(max_ts, ev.get("ts") or 0)
        if ev["event_id"] in seen:
            continue
        seen.add(ev["event_id"])
        fired_one = _match_and_fire_event(ev, active, state, now, live)
        if fired_one:
            fired.append(fired_one)

    time_fired = _run_time_rules(active, state, now, live)
    sensor_fired = _run_sensor_rules(active, state, now, live)
    state["_frigate_seen"] = list(seen)[-500:]
    save_monitor_state(state)
    write_json_atomic(CURSOR_FILE, {"after": max_ts})
    return json.dumps(
        {
            "status": "ok",
            "mode": POLLER_MODE,
            "events_seen": len(events),
            "alerts": len(fired),
            "time_rules_fired": time_fired,
            "sensor_rules_fired": sensor_fired,
            "fired": fired,
        }
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
