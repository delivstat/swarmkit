"""Shared Home Assistant access — token refresh, state reads, camera snapshots.

Used by the monitoring poller to read cloud-locked cameras' on-device motion/
person detection (surfaced as HA binary_sensors) into the same event stream as
Frigate. The devices server keeps its own onboarding logic; this is the small
read-side surface the poller needs, sharing the same token file.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/app/mcp-servers")
from _atomic import write_json_atomic

HA_URL = os.environ.get("HA_URL", "http://localhost:8123").rstrip("/")
HA_CLIENT_ID = "http://localhost:8123/"
DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
HA_TOKEN_FILE = DATA_DIR / "ha_token.json"


def _get(url: str, token: str = "", raw: bool = False, timeout: int = 10):
    hdrs = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout)
        data = resp.read()
        return resp.status, (data if raw else json.loads(data))
    except urllib.error.HTTPError as e:
        return e.code, (b"" if raw else {})
    except Exception:
        return 0, (b"" if raw else {})


def ha_token() -> str:
    """Load the HA access token, refreshing via the stored refresh token if the
    access token has expired (same token file the devices server manages)."""
    if not HA_TOKEN_FILE.exists():
        return os.environ.get("HA_TOKEN", "")
    try:
        data = json.loads(HA_TOKEN_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        return os.environ.get("HA_TOKEN", "")
    access = data.get("access_token", "")
    refresh = data.get("refresh_token", "")
    code, _ = _get(f"{HA_URL}/api/", access)
    if code == 200:
        return access
    if refresh:
        form = (
            f"grant_type=refresh_token&refresh_token={refresh}&client_id={HA_CLIENT_ID}"
        ).encode()
        req = urllib.request.Request(
            f"{HA_URL}/auth/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            tok = json.loads(urllib.request.urlopen(req, timeout=10).read())
            new = tok["access_token"]
            write_json_atomic(HA_TOKEN_FILE, {"access_token": new, "refresh_token": refresh})
            return new
        except Exception:
            pass
    return access


def ha_states(token: str = "") -> list[dict]:
    token = token or ha_token()
    code, body = _get(f"{HA_URL}/api/states", token)
    return body if code == 200 and isinstance(body, list) else []


def ha_camera_snapshot(camera_entity: str, out_path: str, token: str = "") -> str:
    """Fetch a still from an HA camera entity (the cloud-locked camera's image)
    via camera_proxy. Returns the saved path, or '' if unavailable."""
    token = token or ha_token()
    code, raw = _get(f"{HA_URL}/api/camera_proxy/{camera_entity}", token, raw=True)
    if code == 200 and len(raw) > 1000:
        Path(out_path).write_bytes(raw)
        return str(out_path)
    return ""
