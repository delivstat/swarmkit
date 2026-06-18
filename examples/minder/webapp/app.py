# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastapi",
#   "uvicorn[standard]",
#   "httpx",
# ]
# ///
"""Minder Onboarding Web App — setup wizard at http://minder.local.

Takes a fresh Minder box from unboxing to working in under 10 minutes.
No terminal, no .env files, no separate dashboards.

Runs as a FastAPI service inside the swarmkit container, port 80.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from fastapi import Cookie, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI(title="Minder Setup")

DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "minder-config.json"
CAMERAS_FILE = DATA_DIR / "cameras.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
RULES_FILE = DATA_DIR / "rules.json"
AUTH_FILE = DATA_DIR / "authorized_users.json"
EVENTS_FILE = DATA_DIR / "events.json"
WEBAPP_AUTH_FILE = DATA_DIR / "webapp_auth.json"

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "minder"

_active_sessions: dict[str, str] = {}

PUBLIC_PATHS = {"/api/auth/login", "/api/auth/status", "/login", "/static"}

# Shared secret for internal channel adapters (Telegram bot, future WhatsApp
# bot) running on the same box. Generated once, stored in /data.
INTERNAL_TOKEN_FILE = DATA_DIR / "internal_token"


def _internal_token() -> str:
    if INTERNAL_TOKEN_FILE.exists():
        return INTERNAL_TOKEN_FILE.read_text().strip()
    token = secrets.token_hex(24)
    INTERNAL_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    INTERNAL_TOKEN_FILE.write_text(token)
    return token


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)
        if path == "/api/qrcode":
            return await call_next(request)
        internal = request.headers.get("x-minder-internal", "")
        if internal and internal == _internal_token():
            return await call_next(request)
        session = request.cookies.get("minder_session")
        if session and session in _active_sessions:
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login")


app.add_middleware(AuthMiddleware)


@app.on_event("startup")
async def _ensure_internal_token() -> None:
    _internal_token()


@app.on_event("startup")
async def _start_mqtt() -> None:
    """Real-time camera alerts: subscribe to Frigate's MQTT events. Backgrounded;
    the minute poller remains the reconcile backstop if this is off/unavailable."""
    try:
        from mqtt_listener import start_mqtt_listener

        start_mqtt_listener()
    except Exception as e:
        print(f"[mqtt] startup hook failed: {e}", flush=True)


@app.on_event("startup")
async def _start_poll() -> None:
    """Reconcile backstop: poll Frigate events every minute. Deterministic — calls
    poll_events directly (no LLM agent), replacing the retired minder-poll topology
    + poll-frigate cron. Shares dedup/cooldown state with the MQTT path."""
    try:
        from frigate_poller import start_frigate_poller

        start_frigate_poller()
    except Exception as e:
        print(f"[poll] startup hook failed: {e}", flush=True)


@app.on_event("startup")
async def _start_health_monitor() -> None:
    """Active, report-only health monitor: probes services/deps/channels and
    surfaces status on the dashboard + transition alerts on the MQTT bus."""
    try:
        from health_monitor import start_health_monitor

        start_health_monitor()
    except Exception as e:
        print(f"[health] startup hook failed: {e}", flush=True)


@app.on_event("startup")
async def _startup_recovery() -> None:
    """On boot: DIAGNOSE only (never auto-fix — fixes are human-gated). If a
    precious file is corrupt/missing, raise an approval-request alert; the human
    runs /repair to apply the restore. Always take a fresh good-state backup."""
    import minder_ops

    try:
        h = minder_ops.diagnose_and_alert()
        if not h.get("healthy"):
            print(
                f"[recovery] startup health issue (awaiting human /repair): {h.get('files')}",
                flush=True,
            )
        minder_ops.backup()
    except Exception as e:
        print(f"[recovery] startup diagnose failed: {e}", flush=True)


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
SWARMKIT_URL = os.environ.get("SWARMKIT_URL", "http://localhost:8321")

STATIC_DIR = Path(__file__).parent / "static"

import contextlib

import minder_ops

# ---- Ops API — the single backend every channel adapter talks to ----


class OpsMessageRequest(BaseModel):
    text: str
    source: str = "default"
    sender: str = ""


@app.post("/api/ops/message")
async def ops_message(req: OpsMessageRequest):
    """Universal message entry point for channel adapters (Telegram,
    WhatsApp, ...). Returns a structured result envelope."""
    return await minder_ops.handle_message(req.text, req.source, req.sender)


@app.post("/api/ops/setup/ha")
async def ops_setup_ha():
    return await minder_ops.setup_ha()


class OpsSetupCamerasRequest(BaseModel):
    subnet: str = ""


@app.post("/api/ops/setup/cameras")
async def ops_setup_cameras(req: OpsSetupCamerasRequest):
    return await minder_ops.setup_cameras(req.subnet)


# Alerts are delivered over MQTT (topic minder/alerts) now, not polled — adapters
# subscribe as durable per-subscriber sessions. The /api/ops/alerts poll endpoint
# was retired with that change.


class ScenarioRequest(BaseModel):
    text: str


@app.post("/api/ops/scenario")
async def ops_scenario(req: ScenarioRequest):
    """Create a monitoring scenario from natural language (SwarmKit agent)."""
    return await minder_ops.create_scenario(req.text)


# ---- Weather (preset Met.no setup — no API key) ----


class WeatherSetupRequest(BaseModel):
    city: str = ""
    latitude: float = 0.0
    longitude: float = 0.0


@app.get("/api/ops/weather")
async def ops_weather_status():
    """Whether the weather source is set up and where (drives the setup UI)."""
    return minder_ops.weather_status()


@app.post("/api/ops/weather/setup")
async def ops_weather_setup(req: WeatherSetupRequest):
    """Preset weather setup (Met.no, no API key) for a city or coordinates.
    Geocodes the city and configures Home Assistant in one click."""
    return await asyncio.to_thread(minder_ops.setup_weather, req.city, req.latitude, req.longitude)


# ---- Recovery (backup / restore / doctor / health) ----


@app.get("/api/ops/health")
async def ops_health():
    """State + component health: precious-file status, HA/Frigate reachability,
    backup count."""
    return minder_ops.health()


@app.post("/api/ops/diagnose")
async def ops_diagnose():
    """Detect issues; if unhealthy, file a repair as a review item and alert.
    Read-only — fixes are human-gated via /api/ops/approvals/approve."""
    return minder_ops.diagnose_and_alert()


@app.post("/api/ops/backup")
async def ops_backup():
    """Snapshot precious state to the host-bind-mounted backups path."""
    return minder_ops.backup()


@app.post("/api/ops/backup/ha")
async def ops_backup_ha():
    """Tar the HA config volume (config-only) for full-stack DR."""
    return minder_ops.backup_ha_volume()


@app.get("/api/ops/backups")
async def ops_list_backups():
    return {"backups": minder_ops.list_backups()}


class RestoreRequest(BaseModel):
    ts: str = ""


@app.post("/api/ops/restore")
async def ops_restore(req: RestoreRequest):
    """Restore precious files from a backup (latest good copy if ts empty)."""
    return minder_ops.restore(req.ts)


@app.get("/api/ops/approvals")
async def ops_approvals():
    """Pending repair approvals — SwarmKit review items awaiting a human."""
    return {"approvals": minder_ops.list_approvals()}


class ApprovalRequest(BaseModel):
    id: str = ""


@app.post("/api/ops/approvals/approve")
async def ops_approve(req: ApprovalRequest):
    """Human approval — resolves the review item and applies the repair. The
    only path that fixes anything."""
    return await minder_ops.approve_repair(req.id)


@app.post("/api/ops/approvals/reject")
async def ops_reject(req: ApprovalRequest):
    """Human dismissal — resolves the review item rejected; no fix runs."""
    return minder_ops.reject_repair(req.id)


# ---- Config persistence ----


def _load_config() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {"step": 0, "completed": False}


def _save_config(config: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


# ---- Network ----


def _detect_network() -> dict[str, str]:
    hostname = socket.gethostname()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except OSError:
        local_ip = "unknown"

    subnet = ".".join(local_ip.split(".")[:3]) if local_ip != "unknown" else "192.168.0"
    return {"hostname": hostname, "local_ip": local_ip, "subnet": subnet}


# ---- HA helpers ----


def _ha_api(endpoint: str, token: str, method: str = "GET", data: dict | None = None) -> Any:
    url = f"{HA_URL}/api/{endpoint}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return json.loads(raw) if raw else {"error": f"HTTP {e.code}"}
        except (json.JSONDecodeError, ValueError):
            return {"error": f"HTTP {e.code}: {raw[:200]}"}


# ---- Telegram helpers ----


def _verify_telegram_token(token: str) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/getMe"
    req = urllib.request.Request(url, method="GET")
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    if not data.get("ok"):
        raise ValueError("Invalid token")
    return data["result"]


# ---- Ollama helpers ----


def _ollama_api(endpoint: str, method: str = "GET", data: dict | None = None) -> Any:
    url = f"{OLLAMA_URL}/api/{endpoint}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=300)
    return json.loads(resp.read())


# ---- Request models ----


class CameraDiscoverRequest(BaseModel):
    subnet: str = "192.168.0"
    username: str = "admin"
    password: str = "admin123"


class CameraNameRequest(BaseModel):
    ip: str
    name: str


class TelegramVerifyRequest(BaseModel):
    token: str


class AuthUserRequest(BaseModel):
    telegram_id: int | None = None
    username: str = ""
    name: str = ""
    role: str = "member"


class MonitoringRule(BaseModel):
    # The matching engine (frigate.server._rule_cameras / _match_and_fire_event)
    # is the source of truth for the rule shape — this envelope must round-trip
    # it losslessly, not impose a narrower one. Scenario-authored rules carry
    # `cameras` (list) + `target` and no `camera`; the router may add further
    # fields over time. So `camera` is optional, both camera shapes are allowed,
    # and extra fields are preserved (extra="allow") — otherwise re-saving the
    # full list to delete/toggle one rule drops fields or 422s and the whole
    # save fails silently.
    model_config = ConfigDict(extra="allow")

    camera: str = ""
    cameras: list[str] = []
    condition: str = ""
    schedule: str = "always"
    enabled: bool = True
    actions: list[dict] = []
    at_time: str = ""
    target: str = ""
    created_ts: float = 0


class RulesRequest(BaseModel):
    rules: list[MonitoringRule]


class ModelPullRequest(BaseModel):
    model: str


class DeviceControlRequest(BaseModel):
    entity_id: str
    service: str
    data: dict[str, Any] = {}


class HATokenRequest(BaseModel):
    token: str


class CompleteRequest(BaseModel):
    telegram_token: str = ""
    openrouter_key: str = ""
    ha_token: str = ""


# ---- Auth helpers ----


def _load_webapp_auth() -> dict[str, str]:
    if WEBAPP_AUTH_FILE.exists():
        try:
            return json.loads(WEBAPP_AUTH_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {
        "username": DEFAULT_USERNAME,
        "password_hash": hashlib.sha256(DEFAULT_PASSWORD.encode()).hexdigest(),
    }


def _save_webapp_auth(username: str, password: str) -> None:
    WEBAPP_AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    WEBAPP_AUTH_FILE.write_text(
        json.dumps(
            {
                "username": username,
                "password_hash": hashlib.sha256(password.encode()).hexdigest(),
            },
            indent=2,
        )
    )


# ---- Events helpers ----


def _load_events() -> list[dict[str, Any]]:
    if EVENTS_FILE.exists():
        try:
            return json.loads(EVENTS_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def _save_events(events: list[dict[str, Any]]) -> None:
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVENTS_FILE.write_text(json.dumps(events, indent=2))


def _add_event(
    camera: str, condition: str, message: str, snapshot_path: str = "", video_path: str = ""
) -> dict[str, Any]:
    events = _load_events()
    event = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "camera": camera,
        "condition": condition,
        "message": message,
        "snapshot_path": snapshot_path,
        "video_path": video_path,
        "viewed": False,
    }
    events.insert(0, event)
    # Keep last 500 events
    events = events[:500]
    _save_events(events)
    return event


# ---- API Routes ----


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    auth = _load_webapp_auth()
    pw_hash = hashlib.sha256(req.password.encode()).hexdigest()
    if req.username == auth.get("username") and pw_hash == auth.get("password_hash"):
        session_id = secrets.token_hex(32)
        _active_sessions[session_id] = req.username
        response = JSONResponse({"status": "ok"})
        response.set_cookie(
            "minder_session", session_id, httponly=True, samesite="lax", max_age=86400 * 30
        )
        return response
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/api/auth/status")
async def auth_status(minder_session: str = Cookie(None)):
    if minder_session and minder_session in _active_sessions:
        return {"authenticated": True, "username": _active_sessions[minder_session]}
    return {"authenticated": False}


@app.post("/api/auth/logout")
async def logout(minder_session: str = Cookie(None)):
    if minder_session:
        _active_sessions.pop(minder_session, None)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("minder_session")
    return response


@app.post("/api/auth/change-password")
async def change_password(req: ChangePasswordRequest):
    auth = _load_webapp_auth()
    if hashlib.sha256(req.current_password.encode()).hexdigest() != auth.get("password_hash"):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    _save_webapp_auth(auth["username"], req.new_password)
    return {"status": "ok"}


@app.get("/login")
async def login_page():
    return FileResponse(str(STATIC_DIR / "login.html"))


# ---- Events API ----


@app.get("/api/events")
async def list_events(limit: int = 50):
    events = _load_events()
    unviewed = sum(1 for e in events if not e.get("viewed"))
    return {"events": events[:limit], "total": len(events), "unviewed": unviewed}


@app.post("/api/events/{event_id}/view")
async def mark_event_viewed(event_id: str):
    events = _load_events()
    for e in events:
        if e["id"] == event_id:
            e["viewed"] = True
            _save_events(events)
            return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Event not found")


@app.post("/api/events/view-all")
async def mark_all_viewed():
    events = _load_events()
    for e in events:
        e["viewed"] = True
    _save_events(events)
    return {"status": "ok", "count": len(events)}


@app.get("/api/events/{event_id}/snapshot")
async def get_event_snapshot(event_id: str):
    events = _load_events()
    event = next((e for e in events if e["id"] == event_id), None)
    if not event or not event.get("snapshot_path"):
        raise HTTPException(status_code=404, detail="Snapshot not available")
    path = Path(event["snapshot_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Snapshot file missing")
    return FileResponse(str(path), media_type="image/jpeg")


@app.get("/api/events/{event_id}/video")
async def get_event_video(event_id: str):
    events = _load_events()
    event = next((e for e in events if e["id"] == event_id), None)
    if not event or not event.get("video_path"):
        raise HTTPException(status_code=404, detail="Video not available")
    path = Path(event["video_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Video file missing")
    return FileResponse(str(path), media_type="video/mp4")


@app.get("/api/status")
async def get_status():
    config = _load_config()
    return {"config": config, "completed": config.get("completed", False)}


@app.get("/api/network")
async def get_network():
    return _detect_network()


# -- Step 2: Cameras --


@app.post("/api/cameras/discover")
async def discover_cameras(req: CameraDiscoverRequest):
    try:
        result = await minder_ops.setup_cameras(req.subnet)
        config = _load_config()
        config["camera_credentials"] = {
            "subnet": req.subnet,
            "username": req.username,
            "password": req.password,
        }
        _save_config(config)

        cameras = result.get("data", {}).get("cameras", [])
        return {"cameras": cameras, "count": len(cameras), "output": result.get("text", "")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/cameras")
async def list_cameras():
    if not CAMERAS_FILE.exists():
        return {"cameras": [], "count": 0}
    cameras = json.loads(CAMERAS_FILE.read_text())
    return {"cameras": cameras, "count": len(cameras)}


@app.post("/api/cameras/name")
async def name_camera(req: CameraNameRequest):
    if not CAMERAS_FILE.exists():
        raise HTTPException(status_code=404, detail="No cameras discovered")
    cameras = json.loads(CAMERAS_FILE.read_text())
    for cam in cameras:
        if cam["ip"] == req.ip:
            cam["name"] = req.name
            CAMERAS_FILE.write_text(json.dumps(cameras, indent=2))
            return {"status": "ok", "ip": req.ip, "name": req.name}
    raise HTTPException(status_code=404, detail=f"Camera {req.ip} not found")


@app.get("/api/cameras/{ip}/snapshot")
async def get_camera_snapshot(ip: str):
    snap_path = SNAPSHOT_DIR / f"{ip.replace('.', '_')}.jpg"
    if not snap_path.exists():
        raise HTTPException(status_code=404, detail="Snapshot not available")
    return FileResponse(snap_path, media_type="image/jpeg")


@app.get("/api/cameras/{ip}/stream")
async def get_camera_stream(ip: str):
    """Live MJPEG stream from a camera's RTSP feed via ffmpeg."""
    if not CAMERAS_FILE.exists():
        raise HTTPException(status_code=404, detail="No cameras")
    cameras = json.loads(CAMERAS_FILE.read_text())
    cam = next((c for c in cameras if c["ip"] == ip), None)
    if not cam or not cam.get("rtsp_url"):
        raise HTTPException(status_code=404, detail=f"Camera {ip} not found or no RTSP")

    cam_user = os.environ.get("MINDER_CAM_USER", "admin")
    cam_pass = os.environ.get("MINDER_CAM_PASS", "admin123")
    rtsp_url = cam["rtsp_url"].replace("rtsp://", f"rtsp://{cam_user}:{cam_pass}@")

    async def mjpeg_generator():
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-rtsp_transport",
            "tcp",
            "-i",
            rtsp_url,
            "-f",
            "mjpeg",
            "-q:v",
            "5",
            "-r",
            "5",
            "-vf",
            "scale=640:-1",
            "-an",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            buf = b""
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start < 0 or end < 0:
                        break
                    frame = buf[start : end + 2]
                    buf = buf[end + 2 :]
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: "
                        + str(len(frame)).encode()
                        + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
        finally:
            proc.kill()
            await proc.wait()

    from starlette.responses import StreamingResponse

    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# -- Step 3: Home Assistant --


@app.get("/api/ha/status")
async def ha_status():
    try:
        url = f"{HA_URL}/api/"
        req = urllib.request.Request(url, method="GET")
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            return {"running": True, "message": data.get("message", "API running")}
        except urllib.error.HTTPError as e:
            # 401/403 means HA is running but needs auth — that's fine
            return {"running": True, "message": f"Running (HTTP {e.code})"}
    except Exception:
        return {"running": False, "message": "Home Assistant is not reachable"}


@app.post("/api/ha/setup")
async def setup_ha():
    """Auto-setup HA via the ops layer."""
    try:
        result = await minder_ops.setup_ha()
        return {"status": "ok", "message": result.get("text", "Set up")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


POPULAR_INTEGRATIONS = [
    {
        "id": "tuya",
        "name": "Tuya / SmartLife",
        "description": "Official Tuya integration — lights, switches, fans, AC",
        "icon_color": "#f59e0b",
        "icon_letter": "T",
        "fields": [
            {
                "name": "user_code",
                "label": "User Code (from iot.tuya.com → Cloud → Link Devices)",
                "type": "text",
            },
        ],
    },
    {
        "id": "localtuya",
        "name": "LocalTuya (pre-installed)",
        "description": "Control Tuya/SmartLife devices locally — no cloud needed after setup",
        "icon_color": "#6366f1",
        "icon_letter": "L",
        "fields": [],
    },
    {
        "id": "zha",
        "name": "Zigbee (ZHA)",
        "description": "Zigbee devices via a USB coordinator (built-in)",
        "icon_color": "#22c55e",
        "icon_letter": "Z",
        "fields": [],
    },
    {
        "id": "esphome",
        "name": "ESPHome",
        "description": "DIY ESP32/ESP8266 devices (built-in)",
        "icon_color": "#3b82f6",
        "icon_letter": "E",
        "fields": [],
    },
]


@app.get("/api/ha/integrations")
async def ha_integrations():
    """List popular pre-installed integrations and their setup status."""
    token = _get_ha_token()
    installed: list[str] = []
    if token:
        try:
            entries = _ha_api("config/config_entries/entry", token)
            if isinstance(entries, list):
                installed = [e.get("domain", "") for e in entries]
        except Exception:
            pass

    result = []
    for integ in POPULAR_INTEGRATIONS:
        result.append(
            {
                **integ,
                "installed": integ["id"] in installed,
            }
        )
    return {"integrations": result}


class IntegrationSetupRequest(BaseModel):
    integration_id: str
    data: dict[str, str] = {}


@app.post("/api/ha/integrations/setup")
async def setup_integration(req: IntegrationSetupRequest):
    """Start a config flow for an integration via HA's API."""
    try:
        token = _get_ha_token()
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"HA token error: {e}"}, status_code=200)
    if not token:
        return JSONResponse({"status": "error", "message": "HA not set up yet"}, status_code=200)

    try:
        start_resp = _ha_api(
            "config/config_entries/flow",
            token,
            "POST",
            {"handler": req.integration_id, "show_advanced_options": False},
        )
        if not start_resp or not isinstance(start_resp, dict):
            return {"status": "error", "message": "No response from HA"}
        if start_resp.get("error"):
            return {"status": "error", "message": str(start_resp["error"])}

        flow_id = start_resp.get("flow_id")
        if not flow_id:
            return {"status": "error", "message": f"Failed to start flow: {start_resp}"}

        if not req.data:
            return {"status": "flow_started", "flow_id": flow_id, "step": start_resp}

        result = _ha_api(
            f"config/config_entries/flow/{flow_id}",
            token,
            "POST",
            req.data,
        )
        return _parse_flow_result(result, req.integration_id)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)


class FlowStepRequest(BaseModel):
    flow_id: str
    data: dict[str, str] = {}


@app.post("/api/ha/integrations/flow-step")
async def continue_flow(req: FlowStepRequest):
    """Continue a multi-step config flow."""
    try:
        token = _get_ha_token()
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"HA token error: {e}"}, status_code=200)
    if not token:
        return JSONResponse({"status": "error", "message": "HA not set up yet"}, status_code=200)
    try:
        result = _ha_api(
            f"config/config_entries/flow/{req.flow_id}",
            token,
            "POST",
            req.data,
        )
        return _parse_flow_result(result, "integration")
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)


def _parse_flow_result(result: dict | None, name: str) -> dict:
    if not result or not isinstance(result, dict):
        return {"status": "error", "message": "No response from Home Assistant"}
    if result.get("error"):
        return {"status": "error", "message": str(result["error"])}
    if result.get("type") == "create_entry":
        return {"status": "ok", "message": f"{name} configured successfully"}

    errors = result.get("errors")
    if errors:
        placeholders = result.get("description_placeholders") or {}
        msg = placeholders.get("msg") or str(errors)
        return {"status": "error", "message": msg, "step": result}

    if result.get("type") == "form":
        fields = []
        for schema in result.get("data_schema") or []:
            # Check for QR code selector
            selector = schema.get("selector") or {}
            qr_data = (selector.get("qr_code") or {}).get("data", "")

            if qr_data:
                fields.append(
                    {
                        "name": schema.get("name", "qr"),
                        "label": "QR Code",
                        "type": "qr",
                        "default": qr_data,
                        "required": False,
                    }
                )
                continue

            field = {
                "name": schema["name"],
                "label": schema["name"].replace("_", " ").title(),
                "type": "password" if "password" in schema.get("name", "") else "text",
                "required": schema.get("required", False),
                "default": str(schema.get("default", "") or ""),
            }
            if schema.get("type") == "select" or "options" in schema:
                field["type"] = "select"
                field["options"] = schema.get("options", [])
            fields.append(field)

        return {
            "status": "next_step",
            "flow_id": result.get("flow_id"),
            "step_id": result.get("step_id"),
            "fields": fields,
        }
    return {"status": "pending", "step": result}


HA_TOKEN_FILE = DATA_DIR / "ha_token.json"


def _get_ha_token() -> str:
    """Load HA token from token file (auto-managed), config, or env."""
    if HA_TOKEN_FILE.exists():
        try:
            data = json.loads(HA_TOKEN_FILE.read_text())
            token = data.get("access_token", "")
            if token:
                # Try the token, refresh if expired
                try:
                    req = urllib.request.Request(
                        f"{HA_URL}/api/",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    urllib.request.urlopen(req, timeout=5)
                    return token
                except urllib.error.HTTPError:
                    # Try refresh
                    refresh = data.get("refresh_token", "")
                    if refresh:
                        form = f"grant_type=refresh_token&refresh_token={refresh}&client_id=http://localhost:8123/".encode()
                        req = urllib.request.Request(
                            f"{HA_URL}/auth/token",
                            data=form,
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            method="POST",
                        )
                        try:
                            resp = urllib.request.urlopen(req, timeout=10)
                            tokens = json.loads(resp.read())
                            new_token = tokens["access_token"]
                            HA_TOKEN_FILE.write_text(
                                json.dumps(
                                    {
                                        "access_token": new_token,
                                        "refresh_token": refresh,
                                    },
                                    indent=2,
                                )
                            )
                            return new_token
                        except Exception:
                            pass
                except Exception:
                    pass
        except (json.JSONDecodeError, ValueError):
            pass
    config = _load_config()
    return config.get("ha_token", os.environ.get("HA_TOKEN", ""))


# Controllable HA domains and, per domain, the services the dashboard may call
# and the data keys each service accepts. This is an allowlist — the control
# endpoint refuses anything not listed, so the deterministic passthrough can
# never call an arbitrary HA service. (Domain is derived from the entity_id.)
_DEVICE_DOMAINS = ("light", "switch", "fan", "climate", "cover", "lock", "media_player")
# Commanded service -> the state it produces, for optimistic UI (HA's own state
# read lags for cloud devices). Services not listed (toggle, sliders) fall back
# to the re-read.
_OPTIMISTIC_STATE = {
    "turn_on": "on",
    "turn_off": "off",
    "lock": "locked",
    "unlock": "unlocked",
    "open_cover": "open",
    "close_cover": "closed",
}
_CONTROL_ALLOW: dict[str, dict[str, set[str]]] = {
    "light": {
        "turn_on": {"brightness_pct", "rgb_color", "color_temp_kelvin"},
        "turn_off": set(),
        "toggle": set(),
    },
    "switch": {"turn_on": set(), "turn_off": set(), "toggle": set()},
    "fan": {"turn_on": set(), "turn_off": set(), "toggle": set(), "set_percentage": {"percentage"}},
    "climate": {
        "turn_on": set(),
        "turn_off": set(),
        "set_temperature": {"temperature"},
        "set_hvac_mode": {"hvac_mode"},
    },
    "cover": {
        "open_cover": set(),
        "close_cover": set(),
        "stop_cover": set(),
        "set_cover_position": {"position"},
    },
    "lock": {"lock": set(), "unlock": set()},
    "media_player": {
        "turn_on": set(),
        "turn_off": set(),
        "media_play_pause": set(),
        "volume_set": {"volume_level"},
    },
}
# Attributes the dashboard needs to render per-type controls (brightness slider,
# thermostat target, cover position, …). Pulled through verbatim from HA state.
_DEVICE_ATTRS = (
    "brightness",
    "rgb_color",
    "color_temp_kelvin",
    "supported_color_modes",
    "percentage",
    "current_temperature",
    "temperature",
    "min_temp",
    "max_temp",
    "target_temp_step",
    "hvac_modes",
    "current_position",
    "volume_level",
    "supported_features",
)


def _device_from_entity(entity: dict) -> dict | None:
    eid = entity.get("entity_id", "")
    domain = eid.split(".")[0] if "." in eid else ""
    if domain not in _DEVICE_DOMAINS:
        return None
    attrs = entity.get("attributes", {})
    return {
        "id": eid,
        "name": attrs.get("friendly_name", eid),
        "type": domain,
        "state": entity.get("state", "unknown"),
        "attributes": {k: attrs[k] for k in _DEVICE_ATTRS if k in attrs},
    }


def _dedupe_devices(devices: list[dict]) -> list[dict]:
    """Drop ghost duplicates. When an integration is re-set-up, HA abandons the
    old entities (left permanently `unavailable`) and creates fresh ones with a
    `_2` suffix but the SAME friendly name. So when several entities share a name,
    keep the available one(s) and drop the unavailable twins; a name with only
    unavailable entities keeps one (so a genuinely offline device still shows)."""
    by_name: dict[str, list[dict]] = {}
    for d in devices:
        by_name.setdefault(d["name"], []).append(d)
    out: list[dict] = []
    for group in by_name.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        live = [d for d in group if d["state"] not in ("unavailable", "unknown")]
        out.extend(live or group[:1])
    return out


@app.get("/api/ha/devices")
async def ha_devices():
    token = _get_ha_token()
    if not token:
        return {"devices": [], "message": "No HA token configured"}
    try:
        states = _ha_api("states", token)
        devices = _dedupe_devices([d for e in states if (d := _device_from_entity(e))])
        return {"devices": devices, "count": len(devices)}
    except Exception as e:
        return {"devices": [], "message": str(e)}


@app.post("/api/devices/control")
async def control_device_endpoint(req: DeviceControlRequest):
    """Deterministic device control — call an HA service on an exact entity, with
    no LLM and no fuzzy matching. The service + data are checked against the
    per-domain allowlist (_CONTROL_ALLOW), so this can only do what the dashboard
    UI exposes. Returns the entity's new state for the UI to reflect."""
    token = _get_ha_token()
    if not token:
        raise HTTPException(status_code=400, detail="Home Assistant is not connected")
    domain = req.entity_id.split(".")[0] if "." in req.entity_id else ""
    allowed = _CONTROL_ALLOW.get(domain)
    if allowed is None:
        raise HTTPException(status_code=400, detail=f"Unsupported device type: {domain or '?'}")
    if req.service not in allowed:
        raise HTTPException(
            status_code=400, detail=f"Service '{req.service}' not allowed for {domain}"
        )
    extra = set(req.data) - allowed[req.service]
    if extra:
        raise HTTPException(status_code=400, detail=f"Unexpected parameters: {sorted(extra)}")
    try:
        _ha_api(
            f"services/{domain}/{req.service}",
            token,
            "POST",
            {"entity_id": req.entity_id, **req.data},
        )
        # HA reports state with eventual consistency — Tuya/cloud switches can lag
        # seconds, so a re-read here returns the STALE state. Reflect the commanded
        # state optimistically (we know what we just did); the dashboard reconciles
        # against HA on its next refresh. Attributes come from the (best-effort)
        # re-read for sliders that have a real value to show.
        state = _ha_api(f"states/{req.entity_id}", token)
        dev = _device_from_entity(state) if isinstance(state, dict) else None
        # Apply the optimistic state only if the device is actually reachable —
        # never paint "on" over an unavailable device that didn't switch.
        if (
            dev is not None
            and req.service in _OPTIMISTIC_STATE
            and dev["state"] not in ("unavailable", "unknown")
        ):
            dev["state"] = _OPTIMISTIC_STATE[req.service]
        return {"status": "ok", "device": dev}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"HA control failed: {e}") from e


# -- Step 4: Telegram --


@app.get("/api/qrcode")
async def generate_qrcode(url: str):
    """Generate a QR code PNG for the given URL."""
    import io

    try:
        import qrcode

        img = qrcode.make(url, box_size=6, border=2)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except ImportError as e:
        raise HTTPException(status_code=501, detail="qrcode not installed") from e


@app.post("/api/telegram/verify")
async def verify_telegram(req: TelegramVerifyRequest):
    try:
        bot_info = await asyncio.to_thread(_verify_telegram_token, req.token)
        config = _load_config()
        config["telegram_token"] = req.token
        config["telegram_bot"] = bot_info
        _save_config(config)
        return {"status": "ok", "bot": bot_info}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid token: {e}") from e


# ---- Messaging channels (multi-adapter: Telegram, Discord, ...) ----
#
# Each adapter idles until its token is configured here (alert_bus.wait_for_token
# reads minder-config.json), so enabling a channel needs no container restart.
CHANNELS = [
    {
        "id": "telegram",
        "name": "Telegram",
        "help": "Create a bot with @BotFather, paste its token, then add the bot "
        "to your family group and send /start there.",
    },
    {
        "id": "discord",
        "name": "Discord",
        "help": "Create a bot in the Discord Developer Portal, enable the Message "
        "Content intent, invite it to your server, then paste its token and "
        "@mention it in the channel you want Minder to use.",
    },
]


def _channel_token_set(config: dict[str, Any], cid: str) -> bool:
    ch = (config.get("channels") or {}).get(cid) or {}
    legacy = config.get("telegram_token") if cid == "telegram" else ""
    return bool(ch.get("token") or legacy or os.environ.get(f"MINDER_{cid.upper()}_TOKEN"))


@app.get("/api/ops/channels")
async def list_channels():
    """Messaging providers + their config status (drives Settings -> Channels)."""
    config = _load_config()
    out = []
    for c in CHANNELS:
        ch = (config.get("channels") or {}).get(c["id"]) or {}
        configured = _channel_token_set(config, c["id"])
        out.append({**c, "enabled": ch.get("enabled", configured), "configured": configured})
    return {"channels": out}


class ChannelConfigRequest(BaseModel):
    token: str = ""
    enabled: bool = True


@app.post("/api/ops/channels/{channel_id}")
async def configure_channel(channel_id: str, req: ChannelConfigRequest):
    """Enable/configure a messaging channel. Writes the token to config; the
    adapter (idling on wait_for_token) picks it up within ~15s — no restart."""
    if channel_id not in {c["id"] for c in CHANNELS}:
        raise HTTPException(status_code=404, detail="unknown channel")
    config = _load_config()
    channels = config.setdefault("channels", {})
    ch = channels.setdefault(channel_id, {})
    if req.token:
        if channel_id == "telegram":  # verify the bot token up front
            try:
                config["telegram_bot"] = await asyncio.to_thread(_verify_telegram_token, req.token)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid token: {e}") from e
            config["telegram_token"] = req.token
        ch["token"] = req.token
    ch["enabled"] = req.enabled
    _save_config(config)
    _write_env_file(config)
    return {"status": "ok", "channel": channel_id, "configured": bool(ch.get("token"))}


# -- Step 4b: Authorized users --


@app.get("/api/users")
async def list_users():
    if AUTH_FILE.exists():
        try:
            return json.loads(AUTH_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {"users": []}


@app.post("/api/users")
async def add_user(req: AuthUserRequest):
    auth = {"users": []}
    if AUTH_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            auth = json.loads(AUTH_FILE.read_text())
    if not auth.get("users"):
        auth["users"] = []

    auth["users"].append(
        {
            "telegram_id": req.telegram_id,
            "username": req.username,
            "name": req.name,
            "role": req.role,
        }
    )
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(auth, indent=2))
    return {"status": "ok", "users": auth["users"]}


@app.delete("/api/users/{index}")
async def remove_user(index: int):
    if not AUTH_FILE.exists():
        raise HTTPException(status_code=404, detail="No users configured")
    auth = json.loads(AUTH_FILE.read_text())
    users = auth.get("users", [])
    if index < 0 or index >= len(users):
        raise HTTPException(status_code=404, detail="User not found")
    removed = users.pop(index)
    AUTH_FILE.write_text(json.dumps(auth, indent=2))
    return {"status": "ok", "removed": removed}


# -- Step 5: AI Models --


@app.get("/api/hardware")
async def detect_hardware():
    gpu_available = False
    gpu_info = ""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            gpu_available = True
            gpu_info = result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError, OSError):
        pass

    import shutil as _shutil

    _total, _used, free = _shutil.disk_usage("/")
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    ram_kb = int(line.split()[1])
                    break
            else:
                ram_kb = 0
    except (FileNotFoundError, ValueError):
        ram_kb = 0

    ram_gb = round(ram_kb / 1024 / 1024, 1)

    recommendation = "cloud"
    if gpu_available:
        recommendation = "local"
    elif ram_gb >= 16:
        recommendation = "local-cpu"

    return {
        "gpu_available": gpu_available,
        "gpu_info": gpu_info,
        "ram_gb": ram_gb,
        "disk_free_gb": round(free / (1024**3), 1),
        "recommendation": recommendation,
    }


@app.get("/api/models")
async def list_models():
    try:
        result = _ollama_api("tags")
        models = [
            {"name": m["name"], "size_gb": round(m.get("size", 0) / (1024**3), 1)}
            for m in result.get("models", [])
        ]
        return {"ollama_running": True, "models": models}
    except Exception:
        return {"ollama_running": False, "models": []}


@app.post("/api/models/pull")
async def pull_model(req: ModelPullRequest):
    try:
        await asyncio.to_thread(_ollama_api, "pull", "POST", {"name": req.model, "stream": False})
        return {"status": "ok", "model": req.model}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# -- Step 6: Monitoring Rules --


@app.get("/api/rules")
async def list_rules():
    if RULES_FILE.exists():
        try:
            return {"rules": json.loads(RULES_FILE.read_text())}
        except (json.JSONDecodeError, ValueError):
            pass
    return {"rules": []}


@app.post("/api/rules")
async def save_rules(req: RulesRequest):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rules = [r.model_dump() for r in req.rules]
    RULES_FILE.write_text(json.dumps(rules, indent=2))
    return {"status": "ok", "count": len(rules)}


# -- Step 7: Complete --


@app.post("/api/complete")
async def complete_setup(req: CompleteRequest):
    config = _load_config()

    if req.telegram_token:
        config["telegram_token"] = req.telegram_token
    if req.openrouter_key:
        config["openrouter_key"] = req.openrouter_key
    if req.ha_token:
        config["ha_token"] = req.ha_token

    config["completed"] = True
    _save_config(config)

    _write_env_file(config)

    return {"status": "ok", "message": "Setup complete! Send /start to your Telegram bot."}


def _write_env_file(config: dict[str, Any]) -> None:
    """Generate .env from the collected config."""
    creds = config.get("camera_credentials", {})
    channels = config.get("channels", {})
    lines = [
        f"MINDER_TELEGRAM_TOKEN={config.get('telegram_token', '')}",
        f"MINDER_DISCORD_TOKEN={(channels.get('discord') or {}).get('token', '')}",
        f"OPENROUTER_API_KEY={config.get('openrouter_key', '')}",
        f"HA_TOKEN={config.get('ha_token', '')}",
        f"MINDER_SUBNET={creds.get('subnet', '192.168.0')}",
        f"MINDER_CAM_USER={creds.get('username', 'admin')}",
        f"MINDER_CAM_PASS={creds.get('password', 'admin123')}",
    ]
    env_path = DATA_DIR / "generated.env"
    env_path.write_text("\n".join(lines) + "\n")


# ---- Static files + SPA fallback ----


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("MINDER_WEBAPP_PORT", "80"))
    uvicorn.run(app, host="0.0.0.0", port=port)
