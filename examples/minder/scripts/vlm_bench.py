"""VLM description benchmark (CPU only).

Compares the post-detection description across models, input resolutions, and
prompts — on the ACTUAL failing image (event c0021edc, 352x288 detect-stream
snapshot) plus a fresh high-res frame from the same camera tier. Everything runs
on CPU (num_gpu=0). Prints each result as it finishes.

Run in-container:  docker compose exec -T minder python /tmp/vlm_bench.py
"""

import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

OLLAMA = "http://localhost:11434/api/chat"
CAM_USER = __import__("os").environ.get("MINDER_CAM_USER", "admin")
CAM_PASS = __import__("os").environ.get("MINDER_CAM_PASS", "")

OPEN_PROMPT = (
    "You are a home security camera assistant. In two or three short, factual "
    "sentences of plain prose (not a list, no preamble), describe what is "
    "happening: people and what they're doing, vehicles, and anything notable. "
    "Ignore any timestamp overlay."
)
CONSTRAINED_PROMPT = (
    "You are a home security assistant. A home security camera detected a vehicle. "
    "Describe only that vehicle, in ONE short, factual sentence (plain prose, no "
    "preamble, no list). State only what is clearly visible — colour, type, and "
    "rough position. Do NOT read, guess, or mention any licence plate, text, sign, "
    "or number. Do NOT mention any person, animal, or vehicle that is not clearly "
    "visible, and do NOT guess how many there are. If a detail is unclear, leave it "
    "out rather than guess. Ignore any timestamp overlay."
)


def b64(p):
    return base64.b64encode(Path(p).read_bytes()).decode()


def describe(model, image_path, prompt, temp):
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt, "images": [b64(image_path)]}],
            "stream": False,
            "think": False,
            "keep_alive": "30s",
            "options": {"temperature": temp, "num_gpu": 0, "num_predict": 160},
        }
    ).encode()
    t0 = time.time()
    req = urllib.request.Request(OLLAMA, data=payload, headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=600).read())
        out = (r.get("message", {}).get("content") or "").strip()
    except Exception as e:
        out = f"<error: {e}>"
    return out, time.time() - t0


def grab_hires(ip, out, long_edge=1024):
    """One frame from the camera's MAIN stream (subtype=0, full res), downscaled."""
    url = f"rtsp://{CAM_USER}:{CAM_PASS}@{ip}:554/cam/realmonitor?channel=1&subtype=0"
    raw = out + ".orig.jpg"
    cmd = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", url, "-frames:v", "1", "-q:v", "2", raw]
    subprocess.run(cmd, capture_output=True, timeout=30)
    if not Path(raw).exists():
        return None, "?"
    from PIL import Image

    im = Image.open(raw)
    w, h = im.size
    scale = long_edge / max(w, h)
    if scale < 1:
        im = im.resize((int(w * scale), int(h * scale)))
    im.convert("RGB").save(out, quality=85)
    return out, f"{im.size[0]}x{im.size[1]} (src {w}x{h})"


def main():
    # 1. the actual failing image (event c0021edc)
    events = json.loads(Path("/data/events.json").read_text())
    e = next((x for x in events if x["id"] == "c0021edc"), None)
    failing = e["snapshot_path"] if e else None
    print(f"failing image (c0021edc): {failing}", flush=True)

    # 2. a fresh high-res frame from Porch-1 (192.168.0.101)
    hires, dim = grab_hires("192.168.0.101", "/tmp/hires_porch.jpg")
    print(f"hi-res frame (Porch-1 main stream): {hires}  {dim}", flush=True)

    MODELS = ["llava-phi3", "granite3.2-vision", "qwen2.5vl:3b"]

    runs = []
    # reproduction: incumbent model + open prompt on the 352x288 image
    runs.append(("llava-phi3", failing, "352x288", "OPEN", OPEN_PROMPT, 0.3))
    # constrained prompt, each model, on the low-res failing image
    for m in MODELS:
        runs.append((m, failing, "352x288", "CONSTRAINED", CONSTRAINED_PROMPT, 0.1))
    # constrained prompt, each model, on the hi-res frame
    if hires:
        for m in MODELS:
            runs.append((m, hires, "hi-res", "CONSTRAINED", CONSTRAINED_PROMPT, 0.1))

    print(f"\n{'=' * 70}\nRunning {len(runs)} inferences on CPU\n{'=' * 70}", flush=True)
    for i, (model, img, res, ptype, prompt, temp) in enumerate(runs, 1):
        if not img:
            continue
        out, dt = describe(model, img, prompt, temp)
        print(f"\n[{i}/{len(runs)}] {model}  |  {res}  |  {ptype}  |  {dt:.1f}s", flush=True)
        print(f"    {out}", flush=True)
    print(f"\n{'=' * 70}\nDONE\n{'=' * 70}", flush=True)


if __name__ == "__main__":
    main()
