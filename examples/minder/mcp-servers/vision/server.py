"""Minder Vision MCP Server — image analysis via local vision model.

Wraps Ollama's vision model (Gemma 4 E2B) to analyse camera snapshots.
Provides tools for scene description and condition checking.
"""

import base64
import contextlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("minder-vision")

# Shared YOLO detector — used for object-presence conditions (person/car/animal)
sys.path.insert(0, "/app/mcp-servers/detect")
try:
    import detector as _yolo
except Exception:
    _yolo = None

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# llava-phi3 (3.8B): on this class of box it's the sweet spot — it does NOT
# hallucinate people (moondream did; that's disqualifying for security), and
# warm it answers in ~5s on CPU. minicpm-v (8B) / qwen2.5vl (3B) are too slow
# for CPU image inference here (>300s).
VISION_MODEL = os.environ.get("MINDER_VISION_MODEL", "llava-phi3")
# Ollama GPU layers for the VLM. 0 = run entirely on CPU/RAM (default) so the
# VLM never evicts the reasoning model from VRAM. On this box GPU model-loading
# thrashes (100-240s cold) because llama3.2 owns the VRAM, while CPU loads
# straight into RAM. >0 to use the GPU on an appliance with its own headroom.
VISION_NUM_GPU = int(os.environ.get("MINDER_VISION_NUM_GPU", "0"))
# Describe provider: "ollama" (local, default — zero cloud) or "openrouter" (a cloud
# VLM for higher-fidelity scene answers; opt-in, for testing the quality ceiling).
# Cloud failure falls back to the local VLM, so behaviour degrades gracefully.
DESCRIBE_PROVIDER = os.environ.get("MINDER_DESCRIBE_PROVIDER", "ollama").lower()
CLOUD_VISION_MODEL = os.environ.get("MINDER_CLOUD_VISION_MODEL", "google/gemini-2.5-flash")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_KEY = os.environ.get("MINDER_OPENROUTER_KEY", "") or os.environ.get(
    "OPENROUTER_API_KEY", ""
)


# Keep the VLM resident in RAM so only the first query pays the cold-load cost
# (~75s); subsequent queries are ~5s. -1 = never unload. RAM is cheap (36GB).
# Ollama wants an int (-1) or a duration string ("24h") — a numeric *string*
# like "-1" is a 400, so coerce numerics to int.
def _keep_alive(raw: str) -> int | str:
    try:
        return int(raw)
    except ValueError:
        return raw


VISION_KEEP_ALIVE = _keep_alive(os.environ.get("MINDER_VISION_KEEP_ALIVE", "-1"))
DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
SNAPSHOT_DIR = DATA_DIR / "snapshots"
CHECKS_FILE = DATA_DIR / "vision_checks.json"


def _record_check(camera: str, condition: str, match: bool, answer: str) -> None:
    """Append a check result so the bot can format replies without LLM text."""
    checks = []
    if CHECKS_FILE.exists():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            checks = json.loads(CHECKS_FILE.read_text())
    checks.append(
        {
            "ts": time.time(),
            "camera": camera,
            "condition": condition,
            "match": match,
            "answer": answer,
        }
    )
    CHECKS_FILE.write_text(json.dumps(checks[-50:], indent=2))


def _query_vision_cloud(image_b64: str, prompt: str) -> dict | None:
    """Answer with a cloud VLM (OpenRouter, OpenAI-compatible chat). Returns None
    without a key or on ANY error so the caller falls back to the local VLM."""
    if not OPENROUTER_KEY:
        return None
    payload = json.dumps(
        {
            "model": CLOUD_VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 256,
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
        start = time.monotonic()
        result = json.loads(urllib.request.urlopen(req, timeout=180).read())
        elapsed_ms = int((time.monotonic() - start) * 1000)
    except Exception:
        return None
    content = (result.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return {"response": content or "", "latency_ms": elapsed_ms}


def _query_vision(image_b64: str, prompt: str) -> dict:
    if DESCRIBE_PROVIDER == "openrouter" and OPENROUTER_KEY:
        cloud = _query_vision_cloud(image_b64, prompt)
        if cloud and cloud.get("response"):
            return cloud
        # cloud miss/failure → fall through to the local VLM
    # num_gpu=0 pins the VLM to CPU/RAM so it never contends with the reasoning
    # model for VRAM. On a shared box the on-demand VLM is occasional, so a
    # slower CPU pass beats minutes of GPU model-swapping/thrash. Set
    # MINDER_VISION_NUM_GPU>0 on a dedicated-GPU appliance to use the GPU.
    # 128 tokens is plenty for a condition answer + brief scene; keeping it low
    # matters because CPU decode time scales with tokens.
    options: dict = {"temperature": 0.1, "num_predict": 128, "num_gpu": VISION_NUM_GPU}
    payload = json.dumps(
        {
            "model": VISION_MODEL,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "stream": False,
            "think": False,
            "keep_alive": VISION_KEEP_ALIVE,
            "options": options,
        }
    ).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    start = time.monotonic()
    resp = urllib.request.urlopen(req, timeout=180)
    result = json.loads(resp.read())
    elapsed_ms = int((time.monotonic() - start) * 1000)

    return {
        "response": result.get("message", {}).get("content", ""),
        "latency_ms": elapsed_ms,
    }


def _load_image(camera_identifier: str) -> tuple[str, str] | None:
    """Load image from snapshot directory by camera name or IP."""
    cameras_file = Path(os.environ.get("MINDER_DATA_DIR", "/data")) / "cameras.json"
    if cameras_file.exists():
        cameras = json.loads(cameras_file.read_text())
        ident_lower = camera_identifier.lower().strip()
        for cam in cameras:
            if (
                cam.get("name", "").lower() == ident_lower
                or cam.get("ip", "") == ident_lower
                or ident_lower in cam.get("name", "").lower()
            ):
                ip = cam["ip"]
                snap = SNAPSHOT_DIR / f"{ip.replace('.', '_')}.jpg"
                if snap.exists():
                    return base64.b64encode(snap.read_bytes()).decode(), cam.get("name", ip)

    # Direct path or IP-based filename
    snap = SNAPSHOT_DIR / f"{camera_identifier.replace('.', '_')}.jpg"
    if snap.exists():
        return base64.b64encode(snap.read_bytes()).decode(), camera_identifier
    return None


def _resolve_snapshot_path(camera_identifier: str) -> Path | None:
    """The snapshot file path for a camera by name or IP (mirrors _load_image)."""
    cameras_file = Path(os.environ.get("MINDER_DATA_DIR", "/data")) / "cameras.json"
    if cameras_file.exists():
        cameras = json.loads(cameras_file.read_text())
        ident = camera_identifier.lower().strip()
        for cam in cameras:
            if (
                cam.get("name", "").lower() == ident
                or cam.get("ip", "") == ident
                or ident in cam.get("name", "").lower()
            ):
                snap = SNAPSHOT_DIR / f"{cam['ip'].replace('.', '_')}.jpg"
                if snap.exists():
                    return snap
    snap = SNAPSHOT_DIR / f"{camera_identifier.replace('.', '_')}.jpg"
    return snap if snap.exists() else None


@mcp.tool()
def describe_camera_scene(camera: str) -> str:
    """Describe what a camera currently sees. Provide camera name or IP.
    Returns a natural language description of the scene."""
    img = _load_image(camera)
    if not img:
        return json.dumps({"status": "error", "message": f"No snapshot for '{camera}'"})

    image_b64, cam_name = img
    result = _query_vision(
        image_b64,
        "You are a home security camera AI. Describe what you see in 2-3 sentences. "
        "Focus on: people (how many, what they're doing), vehicles, animals, "
        "anything unusual. If the scene is empty, say so.",
    )

    return json.dumps(
        {
            "camera": cam_name,
            "description": result["response"],
            "latency_ms": result["latency_ms"],
        }
    )


@mcp.tool()
def check_camera_condition(camera: str, condition: str) -> str:
    """Check whether a specific condition is true for a camera.
    Returns YES/NO with explanation.

    Examples:
      check_camera_condition("porch", "is there a person")
      check_camera_condition("gate", "is the gate open")
      check_camera_condition("backyard", "is there an animal")
    """
    img = _load_image(camera)
    if not img:
        return json.dumps({"status": "error", "message": f"No snapshot for '{camera}'"})

    image_b64, cam_name = img

    # Object-presence conditions (person / vehicle / animal) → YOLO detector.
    want = _yolo.classes_for_condition(condition) if _yolo else set()
    snap_path = _resolve_snapshot_path(camera)
    if want and snap_path:
        res = _yolo.detect(str(snap_path), want_classes=want)
        match = res["matched"] > 0
        scene = _yolo.describe_counts(res["counts"])
        _record_check(cam_name, condition, match, scene)
        # Structured data only — the agent composes the user-facing reply.
        return json.dumps(
            {
                "camera": cam_name,
                "you_asked": condition,
                "match": match,
                "match_count": res["matched"],
                "confidence_pct": int(res["best_confidence"] * 100),
                "everything_visible": scene or "nothing",
                "method": "object-detection",
            }
        )

    # Non-object condition → vision-language model
    result = _query_vision(
        image_b64,
        f"Look at this security camera image. {condition}? Describe what you see.",
    )

    response = result["response"].strip().lower()
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
    has_yes = any(s in response for s in yes_signals)
    has_no = any(s in response for s in no_signals)
    match = has_yes and not has_no

    _record_check(cam_name, condition, match, result["response"].strip())

    return json.dumps(
        {
            "camera": cam_name,
            "condition": condition,
            "match": match,
            "answer": response,
            "method": "vlm",
            "latency_ms": result["latency_ms"],
        }
    )


@mcp.tool()
def check_all_cameras(condition: str) -> str:
    """Check a condition across ALL cameras. Returns which cameras matched.

    Examples:
      check_all_cameras("is there a person visible")
      check_all_cameras("is there a vehicle")
    """
    cameras_file = Path(os.environ.get("MINDER_DATA_DIR", "/data")) / "cameras.json"
    if not cameras_file.exists():
        return json.dumps({"status": "error", "message": "No cameras discovered"})

    cameras = json.loads(cameras_file.read_text())
    results = []

    for cam in cameras:
        if not cam.get("rtsp_url"):
            continue
        ip = cam["ip"]
        snap = SNAPSHOT_DIR / f"{ip.replace('.', '_')}.jpg"
        if not snap.exists():
            continue

        image_b64 = base64.b64encode(snap.read_bytes()).decode()
        result = _query_vision(
            image_b64,
            f"Look at this security camera image. {condition}? Describe what you see.",
        )
        response = result["response"].strip().lower()
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
        has_yes = any(s in response for s in yes_signals)
        has_no = any(s in response for s in no_signals)
        match = has_yes and not has_no

        _record_check(cam.get("name", ip), condition, match, result["response"].strip())

        results.append(
            {
                "camera": cam.get("name", ip),
                "ip": ip,
                "match": match,
                "answer": response,
                "latency_ms": result["latency_ms"],
            }
        )

    matches = [r for r in results if r["match"]]
    return json.dumps(
        {
            "condition": condition,
            "total_checked": len(results),
            "matches": matches,
            "no_match_count": len(results) - len(matches),
        }
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
