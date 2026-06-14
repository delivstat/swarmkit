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
    for cam in cameras:
        key = _slug(cam.get("name") or cam["ip"])
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
            "snapshots": {"enabled": True, "retain": {"default": 7}},
            "record": {"enabled": False},
            "objects": objects,
        }
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


@mcp.tool()
def configure_cameras() -> str:
    """Generate Frigate's config from the frigate-tier cameras in the inventory
    and apply it (validated save + restart). Run this after camera discovery or
    whenever the camera list changes. Returns the cameras configured."""
    cameras = _frigate_cameras()
    if not cameras:
        return json.dumps({"status": "error", "message": "No frigate-tier cameras"})
    config_yaml = yaml.safe_dump(_build_config(cameras), sort_keys=False)
    # Frigate validates the config and only applies if valid (400 otherwise),
    # so a bad config can never take detection down.
    status, raw = _http(
        "POST",
        "/api/config/save?save_option=restart",
        body=config_yaml.encode(),
        ctype="text/plain",
    )
    keys = [_slug(c.get("name") or c["ip"]) for c in cameras]
    if status not in (200, 201):
        return json.dumps(
            {
                "status": "error",
                "http": status,
                "message": raw.decode(errors="replace")[:400],
            }
        )
    return json.dumps(
        {
            "status": "ok",
            "cameras_configured": len(cameras),
            "cameras": keys,
            "reloaded": True,
        }
    )


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


def _describe_snapshot(path: str) -> str:
    """Describe an alert snapshot with the local VLM (Ollama). Mirrors Frigate's
    genai handling: a generous timeout, graceful empty string on ANY failure (the
    alert already fired — the description is additive). Runs on CPU by default."""
    try:
        img = base64.b64encode(Path(path).read_bytes()).decode()
    except Exception:
        return ""
    payload = json.dumps(
        {
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "You are a home security camera assistant. In two or three "
                    "short, factual sentences of plain prose (not a list, no preamble), "
                    "describe what is happening: people and what they're doing, vehicles, and "
                    "anything notable. Ignore any timestamp overlay.",
                    "images": [img],
                }
            ],
            "stream": False,
            "think": False,
            "keep_alive": -1,
            # Cap output: uncapped, the VLM rambles for 200+ tokens and blows the
            # timeout on CPU. ~160 covers 2-3 sentences and stays fast when warm.
            "options": {"temperature": 0.3, "num_gpu": DESCRIBE_NUM_GPU, "num_predict": 160},
        }
    ).encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat", data=payload, headers={"Content-Type": "application/json"}
        )
        r = json.loads(urllib.request.urlopen(req, timeout=DESCRIBE_TIMEOUT).read())
        return (r.get("message", {}).get("content") or "").strip()
    except Exception:
        return ""  # graceful — no description, the alert already went out


def _describe_async(snapshot_path: str, cam: str) -> None:
    """Run the VLM description in the background and send it as a follow-up alert.
    Never blocks the alert path (Frigate-style async enrichment); works for both
    Frigate (RTSP) and HA-snapshot (cloud-locked) cameras."""
    if not DESCRIBE_ENABLED or not snapshot_path:
        return

    def _run() -> None:
        desc = _describe_snapshot(snapshot_path)
        if desc:
            write_alert(f"📷 {cam}: {desc}", cam)

    threading.Thread(target=_run, daemon=True).start()


def _fire(rule: dict, ev: dict, now: float, live: bool) -> str:
    """Fire a matched rule — live writes the real alert + runs device actions;
    shadow only logs the would-be alert for parallel-run comparison."""
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
    snapshot_path = ""
    video_path = ""
    ref = ev.get("snapshot_ref") or ""
    if ref and ev.get("source") == "frigate":
        snapshot_path = json.loads(get_event_snapshot(ev["event_id"])).get("path", "")
    elif ref and ev.get("source") == "ha":
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        out = MEDIA_DIR / (ev["event_id"].replace(":", "_") + ".jpg")
        snapshot_path = ha_camera_snapshot(ref.split("ha:", 1)[1], str(out))
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
        write_alert(msg, cam, snapshot_path, video_path)
        # Enrich with a VLM description as a non-blocking follow-up (the verdict
        # alert already went out). Covers RTSP + HA-snapshot cameras.
        _describe_async(snapshot_path, cam)
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
    """Match ONE normalized event against the active rules and fire the first
    match (object label + camera + cooldown). Mutates `state` (cooldown). Shared
    by the cron poller and the MQTT subscriber so they never double-fire."""
    for rule in active:
        if rule.get("at_time"):
            continue  # time rules handled separately
        if not _camera_match(_rule_cameras(rule), ev["camera"]):
            continue
        labels = _condition_to_labels(rule.get("condition", ""))
        if not labels:
            continue  # non-object condition → not answerable from the stream
        if ev["label"] not in labels:
            continue
        key = f"{ev['camera']}|{rule.get('condition')}"
        if now - state.get(key, 0) < ALERT_COOLDOWN_S:
            continue
        state[key] = now
        return {"camera": ev["camera"], "label": ev["label"], "mode": _fire(rule, ev, now, live)}
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
    seen = set(state.get("_frigate_seen", []))
    if ev["event_id"] in seen:
        return None
    seen.add(ev["event_id"])
    fired = _match_and_fire_event(ev, active, state, now, live)
    state["_frigate_seen"] = list(seen)[-500:]
    save_monitor_state(state)
    return fired


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
            "fired": fired,
        }
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
