"""Minder Discovery Service — network scanner for cameras and smart devices.

Scans the local subnet for ONVIF cameras and Tuya/SmartLife devices,
extracts stream URLs, and captures snapshots via ffmpeg.
"""

import hashlib
import datetime
import base64
import os
import re
import json
import socket
import subprocess
import http.client
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path


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


CAMERA_PORTS = {554: "RTSP", 8554: "RTSP-alt", 80: "HTTP", 8080: "HTTP-alt"}
SNAPSHOT_DIR = Path("/data/snapshots")


def _onvif_auth_header(username: str, password: str) -> str:
    nonce = os.urandom(16)
    created = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
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


def _onvif_request(ip: str, path: str, body: str, user: str, pwd: str) -> str:
    auth = _onvif_auth_header(user, pwd)
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
        "POST", path, body=soap,
        headers={"Content-Type": "application/soap+xml; charset=utf-8"},
    )
    resp = conn.getresponse()
    result = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return result


def scan_subnet(subnet: str, timeout: float = 0.8) -> list[str]:
    """Scan a /24 subnet for hosts with camera-related ports open."""
    results = []

    def check(ip: str) -> str | None:
        for port in CAMERA_PORTS:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(timeout)
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
    return results


def probe_onvif(ip: str, user: str, pwd: str) -> Camera:
    """Probe a single IP via ONVIF and return a Camera with metadata + stream URLs."""
    cam = Camera(ip=ip)

    # Device info
    try:
        body = _onvif_request(ip, "/onvif/device_service", "<tds:GetDeviceInformation/>", user, pwd)
        if "NotAuthorized" in body:
            return cam

        for field in ["Manufacturer", "Model", "FirmwareVersion", "SerialNumber"]:
            m = re.search(rf"<[^>]*{field}[^>]*>(.*?)</", body)
            if m:
                attr = {"FirmwareVersion": "firmware", "SerialNumber": "serial"}.get(field, field.lower())
                setattr(cam, attr, m.group(1))
        cam.onvif = True
    except Exception:
        return cam

    # Stream URI
    try:
        profiles = _onvif_request(ip, "/onvif/media_service", "<trt:GetProfiles/>", user, pwd)
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
            uri_body = _onvif_request(ip, "/onvif/media_service", stream_body, user, pwd)
            uri = re.search(r"<[^>]*Uri[^>]*>(rtsp://[^<]+)</", uri_body)
            if uri:
                raw = uri.group(1).replace("&amp;", "&")
                cam.rtsp_url = raw
    except Exception:
        pass

    # Snapshot URI
    try:
        if tokens:
            snap_body = (
                f"<trt:GetSnapshotUri><trt:ProfileToken>{tokens[0]}</trt:ProfileToken>"
                "</trt:GetSnapshotUri>"
            )
            snap_resp = _onvif_request(ip, "/onvif/media_service", snap_body, user, pwd)
            snap = re.search(r"<[^>]*Uri[^>]*>(http[^<]+)</", snap_resp)
            if snap:
                cam.snapshot_url = snap.group(1).replace("&amp;", "&")
    except Exception:
        pass

    return cam


def capture_snapshot(cam: Camera, user: str, pwd: str) -> Path | None:
    """Capture a single frame from a camera's RTSP stream using ffmpeg."""
    if not cam.rtsp_url:
        return None

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    outpath = SNAPSHOT_DIR / f"{cam.ip.replace('.', '_')}.jpg"

    authed_url = cam.rtsp_url.replace("rtsp://", f"rtsp://{user}:{pwd}@")
    try:
        subprocess.run(
            [
                "ffmpeg", "-rtsp_transport", "tcp",
                "-i", authed_url,
                "-frames:v", "1",
                "-y", str(outpath),
            ],
            capture_output=True, timeout=10,
        )
        if outpath.exists() and outpath.stat().st_size > 1000:
            return outpath
    except Exception:
        pass
    return None


def discover_all(subnet: str, user: str, pwd: str) -> list[Camera]:
    """Full discovery pipeline: scan → probe → snapshot."""
    print(f"Scanning {subnet}.0/24...")
    hosts = scan_subnet(subnet)
    print(f"Found {len(hosts)} devices with camera ports open")

    cameras = []
    for ip in hosts:
        cam = probe_onvif(ip, user, pwd)
        if cam.onvif:
            snap = capture_snapshot(cam, user, pwd)
            if snap:
                print(f"  {ip} — {cam.manufacturer} {cam.model} [snapshot: {snap}]")
            else:
                print(f"  {ip} — {cam.manufacturer} {cam.model} [no snapshot]")
            cameras.append(cam)

    return cameras


if __name__ == "__main__":
    subnet = os.environ.get("MINDER_SUBNET", "192.168.0")
    user = os.environ.get("MINDER_CAM_USER", "admin")
    pwd = os.environ.get("MINDER_CAM_PASS", "admin123")

    cameras = discover_all(subnet, user, pwd)

    output = [asdict(c) for c in cameras]
    outfile = Path("/data/cameras.json")
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text(json.dumps(output, indent=2))
    print(f"\n{len(cameras)} cameras saved to {outfile}")
    print(json.dumps(output, indent=2))
