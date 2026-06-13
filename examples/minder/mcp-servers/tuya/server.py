"""Minder Home Assistant MCP Server — setup + smart device control via HA REST API.

Home Assistant runs as a hidden sidecar in the Minder stack.
This server handles:
  1. Automatic HA onboarding (account + token creation)
  2. Device discovery and control via HA's REST API
Supports any device HA integrates with: Tuya/SmartLife, Zigbee, WiFi, IR blasters.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, "/app/mcp-servers")
from _atomic import write_json_atomic  # noqa: E402  (corruption-safe writes)

mcp = FastMCP("minder-devices")

HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
HA_TOKEN_FILE = DATA_DIR / "ha_token.json"

HA_INTERNAL_USER = "minder"
HA_INTERNAL_PASS = "minder-internal-do-not-change"
HA_CLIENT_ID = "http://localhost:8123/"


def _load_ha_token() -> str:
    """Load HA token, auto-refreshing if expired."""
    if HA_TOKEN_FILE.exists():
        try:
            data = json.loads(HA_TOKEN_FILE.read_text())
            access_token = data.get("access_token", "")
            refresh_token = data.get("refresh_token", "")

            # Test if access token still works
            code, _ = _ha_raw(
                f"{HA_URL}/api/",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if code == 200:
                return access_token

            # Try refresh
            if refresh_token:
                form_data = (
                    f"grant_type=refresh_token"
                    f"&refresh_token={refresh_token}"
                    f"&client_id={HA_CLIENT_ID}"
                ).encode()
                req = urllib.request.Request(
                    f"{HA_URL}/auth/token",
                    data=form_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                try:
                    resp = urllib.request.urlopen(req, timeout=10)
                    tokens = json.loads(resp.read())
                    new_access = tokens["access_token"]
                    _save_ha_token(new_access, refresh_token)
                    return new_access
                except Exception:
                    pass
        except (json.JSONDecodeError, ValueError):
            pass
    return os.environ.get("HA_TOKEN", "")


def _save_ha_token(access_token: str, refresh_token: str) -> None:
    write_json_atomic(HA_TOKEN_FILE, {
        "access_token": access_token,
        "refresh_token": refresh_token,
    })


def _ha_raw(url: str, method: str = "GET", data: Any = None,
            headers: dict | None = None, timeout: int = 10) -> tuple[int, Any]:
    """Low-level HTTP request returning (status_code, parsed_body)."""
    body = json.dumps(data).encode() if data else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


def _ha_request(endpoint: str, method: str = "GET", data: dict | None = None) -> dict:
    """Authenticated request to HA REST API using stored token."""
    token = _load_ha_token()
    if not token:
        return {"error": "HA not set up yet. Call setup_homeassistant first."}
    url = f"{HA_URL}/api/{endpoint}"
    _, result = _ha_raw(
        url, method, data,
        headers={"Authorization": f"Bearer {token}"},
    )
    return result


# ---- Setup helpers ----


def _login_and_save_token() -> str:
    """Log in with the internal Minder account and save the token."""
    form_data = (
        f"grant_type=password"
        f"&username={HA_INTERNAL_USER}"
        f"&password={HA_INTERNAL_PASS}"
        f"&client_id={HA_CLIENT_ID}"
    ).encode()
    req = urllib.request.Request(
        f"{HA_URL}/auth/token",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        tokens = json.loads(resp.read())
        _save_ha_token(tokens["access_token"], tokens["refresh_token"])
        return json.dumps({"status": "ok", "message": "Logged in and token saved."})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Login failed: {e}"})


# ---- Setup tools ----


@mcp.tool()
def setup_homeassistant() -> str:
    """Set up Home Assistant automatically. Creates the internal account,
    completes onboarding, and generates an access token. Call this once
    during Minder setup. Returns the setup status."""

    # Check if already set up
    existing_token = _load_ha_token()
    if existing_token:
        code, resp = _ha_raw(
            f"{HA_URL}/api/",
            headers={"Authorization": f"Bearer {existing_token}"},
        )
        if code == 200:
            return json.dumps({"status": "ok", "message": "Already set up"})

    # Check if HA is reachable
    code, _ = _ha_raw(f"{HA_URL}/api/")
    if code == 0:
        return json.dumps({"status": "error", "message": "Home Assistant is not reachable"})

    # Check onboarding state
    code, onboarding = _ha_raw(f"{HA_URL}/api/onboarding")
    if code == 404:
        # Onboarding already completed — just need to log in and save token
        return _login_and_save_token()
    if code != 200:
        return json.dumps({"status": "error", "message": f"Cannot check onboarding (HTTP {code})"})

    steps_done = {s["step"]: s["done"] for s in onboarding}

    # Step 1: Create user (if not done)
    if not steps_done.get("user"):
        code, resp = _ha_raw(
            f"{HA_URL}/api/onboarding/users", "POST",
            {
                "client_id": HA_CLIENT_ID,
                "name": "Minder",
                "username": HA_INTERNAL_USER,
                "password": HA_INTERNAL_PASS,
                "language": "en",
            },
        )
        if code != 200 or "auth_code" not in resp:
            return json.dumps({"status": "error", "message": f"Account creation failed: {resp}"})
        auth_code = resp["auth_code"]
    else:
        auth_code = None

    # Step 2: Get access token
    if auth_code:
        code, tokens = _ha_raw(
            f"{HA_URL}/auth/token", "POST",
            None,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # urllib doesn't send form data with _ha_raw, use direct request
        form_data = (
            f"grant_type=authorization_code"
            f"&code={auth_code}"
            f"&client_id={HA_CLIENT_ID}"
        ).encode()
        req = urllib.request.Request(
            f"{HA_URL}/auth/token",
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            tokens = json.loads(resp.read())
        except Exception as e:
            return json.dumps({"status": "error", "message": f"Token exchange failed: {e}"})

        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        if not access_token:
            return json.dumps({"status": "error", "message": "No access token received"})
    else:
        # Already onboarded, try login
        form_data = (
            f"grant_type=password"
            f"&username={HA_INTERNAL_USER}"
            f"&password={HA_INTERNAL_PASS}"
            f"&client_id={HA_CLIENT_ID}"
        ).encode()
        req = urllib.request.Request(
            f"{HA_URL}/auth/token",
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            tokens = json.loads(resp.read())
            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]
        except Exception as e:
            return json.dumps({"status": "error", "message": f"Login failed: {e}"})

    auth_header = {"Authorization": f"Bearer {access_token}"}

    # Step 3: Complete remaining onboarding steps
    for step in ["core_config", "analytics"]:
        if not steps_done.get(step):
            _ha_raw(
                f"{HA_URL}/api/onboarding/{step}", "POST",
                {"client_id": HA_CLIENT_ID},
                headers=auth_header,
            )

    if not steps_done.get("integration"):
        _ha_raw(
            f"{HA_URL}/api/onboarding/integration", "POST",
            {"client_id": HA_CLIENT_ID, "redirect_uri": f"{HA_URL}/"},
            headers=auth_header,
        )

    # Save token for future use
    _save_ha_token(access_token, refresh_token)

    return json.dumps({
        "status": "ok",
        "message": "Home Assistant set up successfully. Account created and token saved.",
    })


@mcp.tool()
def get_ha_status() -> str:
    """Check if Home Assistant is running and set up."""
    code, _ = _ha_raw(f"{HA_URL}/api/")
    if code == 0:
        return json.dumps({"running": False, "setup_done": False})

    token = _load_ha_token()
    if token:
        code2, _ = _ha_raw(
            f"{HA_URL}/api/",
            headers={"Authorization": f"Bearer {token}"},
        )
        return json.dumps({"running": True, "setup_done": code2 == 200})

    return json.dumps({"running": True, "setup_done": False})


# ---- Device tools ----


ACTIONS_FILE = DATA_DIR / "device_actions.json"
DEVICES_CACHE = DATA_DIR / "devices_cache.json"


def _record_action(device: str, action: str, ok: bool, message: str = "") -> None:
    """Append an action result so the bot can format replies without LLM text."""
    import time as _time
    actions = []
    if ACTIONS_FILE.exists():
        try:
            actions = json.loads(ACTIONS_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    actions.append({
        "ts": _time.time(),
        "device": device,
        "action": action,
        "ok": ok,
        "message": message,
    })
    ACTIONS_FILE.write_text(json.dumps(actions[-50:], indent=2))


@mcp.tool()
def list_devices() -> str:
    """List all smart devices available in Home Assistant.
    Returns device names, types (light/switch/fan/etc), and current state."""
    token = _load_ha_token()
    if not token:
        return json.dumps({
            "status": "setup_required",
            "message": "Call setup_homeassistant first.",
        })

    states = _ha_request("states")
    if isinstance(states, dict) and "error" in states:
        return json.dumps({"status": "error", "message": states["error"]})

    devices: list[dict[str, Any]] = []
    for entity in states:
        eid = entity.get("entity_id", "")
        domain = eid.split(".")[0] if "." in eid else ""
        if domain in ("light", "switch", "fan", "climate", "cover", "lock", "media_player"):
            devices.append({
                "id": eid,
                "name": entity.get("attributes", {}).get("friendly_name", eid),
                "type": domain,
                "state": entity.get("state", "unknown"),
            })

    DEVICES_CACHE.write_text(json.dumps(devices, indent=2))
    return json.dumps({"devices": devices, "count": len(devices)})


@mcp.tool()
def control_device(
    device_name: str,
    action: Literal["turn_on", "turn_off", "toggle"],
    brightness: int = 0,
    temperature: int = 0,
) -> str:
    """Control a smart device via Home Assistant.

    device_name: the device to control, from the DEVICES list.
    action: turn_on, turn_off, or toggle.
    brightness: optional 1-255 for lights (0 = leave unchanged).
    temperature: optional 16-30 for climate/AC (0 = leave unchanged).
    """
    token = _load_ha_token()
    if not token:
        return json.dumps({"status": "error", "message": "HA not set up yet"})

    states = _ha_request("states")
    if isinstance(states, dict) and "error" in states:
        return json.dumps({"status": "error", "message": states["error"]})

    target = None
    name_lower = device_name.lower().strip()
    words = name_lower.split()
    for entity in states:
        eid = entity.get("entity_id", "")
        domain = eid.split(".")[0] if "." in eid else ""
        if domain not in ("light", "switch", "fan", "climate", "cover", "lock", "media_player"):
            continue
        friendly = entity.get("attributes", {}).get("friendly_name", "").lower()
        if (friendly == name_lower or name_lower in friendly or eid == name_lower
                or (words and all(w in friendly for w in words))):
            target = entity
            break

    if not target:
        _record_action(device_name, action, False, f"Device '{device_name}' not found")
        return json.dumps({"status": "error", "message": f"Device '{device_name}' not found"})

    eid = target["entity_id"]
    friendly_name = target.get("attributes", {}).get("friendly_name", eid)
    domain = eid.split(".")[0]

    service_data: dict[str, Any] = {"entity_id": eid}

    if brightness and action != "turn_off":
        result = _ha_request(f"services/{domain}/turn_on", "POST",
                             {**service_data, "brightness": max(1, min(255, brightness))})
    elif temperature and action != "turn_off":
        result = _ha_request("services/climate/set_temperature", "POST",
                             {**service_data, "temperature": max(16, min(30, temperature))})
    else:
        result = _ha_request(f"services/{domain}/{action}", "POST", service_data)

    failed = isinstance(result, dict) and result.get("error")
    _record_action(friendly_name, action, not failed, str(result.get("error", "")) if failed else "")

    return json.dumps({"status": "ok", "device": friendly_name, "action": action})


@mcp.tool()
def get_device_state(device_name: str) -> str:
    """Get the current state of a specific device."""
    token = _load_ha_token()
    if not token:
        return json.dumps({"status": "error", "message": "HA not set up yet"})

    states = _ha_request("states")
    if isinstance(states, dict) and "error" in states:
        return json.dumps({"status": "error", "message": states["error"]})

    name_lower = device_name.lower()
    for entity in states:
        friendly = entity.get("attributes", {}).get("friendly_name", "").lower()
        eid = entity.get("entity_id", "")
        if friendly == name_lower or name_lower in friendly or eid == name_lower:
            return json.dumps({
                "device": entity.get("attributes", {}).get("friendly_name", eid),
                "state": entity.get("state"),
                "attributes": {
                    k: v for k, v in entity.get("attributes", {}).items()
                    if k in ("brightness", "color_temp", "temperature", "current_temperature",
                             "fan_mode", "hvac_mode", "is_locked")
                },
            })

    return json.dumps({"status": "error", "message": f"Device '{device_name}' not found"})


if __name__ == "__main__":
    mcp.run(transport="stdio")
