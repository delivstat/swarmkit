"""Scenario Studio — custom-detector dataset pipeline (Phase 2/3, slice 1).

Capture frames from a camera, optionally auto-label them with a cloud VLM (OpenRouter,
one-time, opt-in), and export a YOLO-format dataset + an off-box training script. The
trained model runs 24/7 fully local — cloud touches only this one-time setup, never the
monitoring loop. See design/scenario-studio-phase2-quickmode.md.

Auto-label is OFF unless MINDER_OPENROUTER_KEY is set (graceful no-op otherwise), so
merely shipping this never touches the live box.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

DATA_DIR = Path(os.environ.get("MINDER_DATA_DIR", "/data"))
DATASETS_DIR = DATA_DIR / "datasets"
FRIGATE_URL = os.environ.get("FRIGATE_URL", "http://localhost:5000").rstrip("/")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1").rstrip("/")
# Reuse the same OpenRouter key the cloud reasoning provider uses; MINDER_OPENROUTER_KEY
# overrides if a separate key is wanted for labeling.
OPENROUTER_KEY = os.environ.get("MINDER_OPENROUTER_KEY", "") or os.environ.get(
    "OPENROUTER_API_KEY", ""
)
CLOUD_VISION_MODEL = os.environ.get("MINDER_CLOUD_VISION_MODEL", "google/gemini-2.0-flash")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _dir(name: str) -> Path:
    return DATASETS_DIR / _slug(name)


def _meta(name: str) -> dict:
    f = _dir(name) / "meta.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def create_dataset(name: str, prompt: str, class_name: str, camera: str) -> dict:
    """Create a dataset for a custom detector (one class for now)."""
    d = _dir(name)
    (d / "images").mkdir(parents=True, exist_ok=True)
    (d / "labels").mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "slug": _slug(name),
        "prompt": prompt,
        "class_name": _slug(class_name or name),
        "camera": camera,
        "created_ts": time.time(),
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def list_datasets() -> list[dict]:
    if not DATASETS_DIR.exists():
        return []
    out = []
    for d in sorted(DATASETS_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = _meta(d.name)
        if not m:
            continue
        m["images"] = len(list((d / "images").glob("*.jpg")))
        m["labeled"] = len(list((d / "labels").glob("*.txt")))
        m["model"] = (d / "model.pt").exists()
        out.append(m)
    return out


def _grab_snapshot(camera: str) -> bytes | None:
    """One Frigate snapshot for the camera (slug = Frigate camera key)."""
    slug = _slug(camera)
    try:
        return urllib.request.urlopen(f"{FRIGATE_URL}/api/{slug}/latest.jpg", timeout=8).read()
    except Exception:
        return None


def capture_frames(name: str, count: int = 10, _grab=None) -> dict:
    """Grab `count` snapshots from the dataset's camera into images/. `_grab` is
    injectable for tests."""
    meta = _meta(name)
    if not meta:
        return {"error": "unknown dataset"}
    grab = _grab or _grab_snapshot
    images = _dir(name) / "images"
    saved = 0
    base = int(time.time())
    for i in range(max(1, count)):
        data = grab(meta["camera"])
        if data and len(data) > 1000:
            (images / f"{base}_{i:03d}.jpg").write_bytes(data)
            saved += 1
    return {"captured": saved, "total_images": len(list(images.glob("*.jpg")))}


def _openrouter_boxes(img_b64: str, prompt: str, _post=None) -> list[list[float]]:
    """Ask a cloud VLM (OpenRouter) for normalized [x1,y1,x2,y2] boxes of the prompted
    object. Returns [] without a key or on any error (graceful). `_post` injectable."""
    if not OPENROUTER_KEY:
        return []
    instruction = (
        f"Find every {prompt} in this image. Respond with ONLY a JSON array of boxes, "
        'each as {"box":[x1,y1,x2,y2]} with coordinates normalized 0..1 '
        "(top-left origin). Empty array if none."
    )
    payload = json.dumps(
        {
            "model": CLOUD_VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            "temperature": 0,
        }
    ).encode()
    try:
        if _post:
            content = _post(payload)
        else:
            req = urllib.request.Request(
                f"{OPENROUTER_URL}/chat/completions",
                data=payload,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                },
            )
            r = json.loads(urllib.request.urlopen(req, timeout=60).read())
            content = (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return _parse_boxes(content)
    except Exception:
        return []


def _parse_boxes(content: str) -> list[list[float]]:
    """Parse the VLM's reply into a list of normalized [x1,y1,x2,y2] (clamped)."""
    s = (content or "").strip()
    start, end = s.find("["), s.rfind("]")
    if not (0 <= start < end):
        return []
    try:
        arr = json.loads(s[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    boxes = []
    for item in arr if isinstance(arr, list) else []:
        b = item.get("box") if isinstance(item, dict) else item
        if isinstance(b, list) and len(b) == 4:
            x1, y1, x2, y2 = (max(0.0, min(1.0, float(v))) for v in b)
            if x2 > x1 and y2 > y1:
                boxes.append([x1, y1, x2, y2])
    return boxes


def _to_yolo(boxes: list[list[float]]) -> str:
    """Normalized [x1,y1,x2,y2] -> YOLO 'class cx cy w h' lines (single class 0)."""
    lines = []
    for x1, y1, x2, y2 in boxes:
        lines.append(f"0 {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}")
    return "\n".join(lines)


def autolabel(name: str, _box_fn=None) -> dict:
    """Auto-label every captured image via the cloud VLM (one label file each).
    No-op without an OpenRouter key. `_box_fn` injectable for tests."""
    meta = _meta(name)
    if not meta:
        return {"error": "unknown dataset"}
    if not OPENROUTER_KEY and not _box_fn:
        return {"error": "no OpenRouter key configured", "labeled": 0}
    box_fn = _box_fn or (lambda b64: _openrouter_boxes(b64, meta["prompt"]))
    images = _dir(name) / "images"
    labels = _dir(name) / "labels"
    labeled = 0
    for img in sorted(images.glob("*.jpg")):
        b64 = base64.b64encode(img.read_bytes()).decode()
        boxes = box_fn(b64)
        (labels / f"{img.stem}.txt").write_text(_to_yolo(boxes))
        labeled += 1
    return {"labeled": labeled, "model": CLOUD_VISION_MODEL}


# Off-box training kit. The detector trains on a GPU machine (Colab / a desktop)
# and only the resulting best.pt is imported back — the box never trains. EPOCHS/
# IMGSZ are env-overridable so the user can tune without editing the file.
_TRAIN_PY = """\
#!/usr/bin/env python3
\"\"\"Train a Minder custom detector (YOLOv8-n) from this dataset, off-box on a GPU.
    pip install ultralytics  &&  python train.py
Outputs minder_model.pt (= best.pt) — import THAT file back into Minder.\"\"\"
import os, shutil
from ultralytics import YOLO

EPOCHS = int(os.environ.get("EPOCHS", "100"))
IMGSZ = int(os.environ.get("IMGSZ", "640"))

r = YOLO("yolov8n.pt").train(data="data.yaml", epochs=EPOCHS, imgsz=IMGSZ, batch=16)
best = os.path.join(r.save_dir, "weights", "best.pt")
shutil.copy(best, "minder_model.pt")
print("\\nDONE -> minder_model.pt  (import this into Minder: Studio -> Deploy)")
"""

_RUN_SH = """\
#!/usr/bin/env bash
# One command to train off-box: ./run_training.sh  (needs Python + a GPU)
set -e
pip install -q ultralytics
EPOCHS="${EPOCHS:-100}" IMGSZ="${IMGSZ:-640}" python train.py
echo "Trained -> minder_model.pt. Import it in Minder: Studio -> Deploy."
"""


def _colab_ipynb(name: str) -> str:
    """A Colab notebook: upload this zip, train on the free GPU, download the model."""
    cells = [
        (
            "markdown",
            [
                f"# Train the Minder detector: {name}\\n",
                "Runtime -> Change runtime type -> **GPU**, then run each cell.",
            ],
        ),
        (
            "code",
            [
                "from google.colab import files\\n",
                "up = files.upload()  # upload the *_dataset.zip from Minder\\n",
                "import zipfile, os\\n",
                "z = next(iter(up))\\n",
                "zipfile.ZipFile(z).extractall('ds'); os.chdir('ds')",
            ],
        ),
        ("code", ["!pip -q install ultralytics\\n", "!python train.py"]),
        (
            "code",
            [
                "from google.colab import files\\n",
                "files.download('minder_model.pt')  # import this into Minder",
            ],
        ),
    ]
    nb = {
        "cells": [
            {
                "cell_type": t,
                "metadata": {},
                "source": s,
                **({"outputs": [], "execution_count": None} if t == "code" else {}),
            }
            for t, s in cells
        ],
        "metadata": {"accelerator": "GPU"},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(nb, indent=1)


def export(name: str) -> dict:
    """Write the YOLO dataset + a complete off-box training kit (train.py,
    run_training.sh, a Colab notebook, README) and zip it. Returns the zip path."""
    meta = _meta(name)
    if not meta:
        return {"error": "unknown dataset"}
    d = _dir(name)
    cls = meta.get("class_name", "object")
    (d / "data.yaml").write_text(f"path: .\ntrain: images\nval: images\nnames:\n  0: {cls}\n")
    (d / "train.py").write_text(_TRAIN_PY)
    (d / "run_training.sh").write_text(_RUN_SH)
    (d / "train_colab.ipynb").write_text(_colab_ipynb(meta["name"]))
    (d / "README.txt").write_text(
        f"Minder custom detector: {meta['name']}  (detects: {meta.get('prompt', cls)})\n"
        f"{len(list((d / 'labels').glob('*.txt')))} labeled / "
        f"{len(list((d / 'images').glob('*.jpg')))} frames.\n\n"
        "TRAIN OFF-BOX (the appliance never trains):\n"
        "  Easiest — Colab (free GPU): upload train_colab.ipynb to https://colab.research.google.com,\n"
        "    set runtime to GPU, run the cells (it asks for this zip), download minder_model.pt.\n"
        "  Or on any machine with a GPU:  ./run_training.sh   (or: pip install ultralytics && python train.py)\n"
        "  Tune with EPOCHS=200 IMGSZ=640 ./run_training.sh\n\n"
        "IMPORT BACK: Minder dashboard -> Studio -> your dataset -> Deploy -> upload minder_model.pt.\n"
        "The trained detector then runs on the appliance (no cloud, no GPU needed for inference).\n"
    )
    zip_path = d.parent / f"{meta['slug']}_dataset.zip"
    kit = ("data.yaml", "train.py", "run_training.sh", "train_colab.ipynb", "README.txt")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in kit:
            zf.write(d / f, f)
        for sub in ("images", "labels"):
            for p in sorted((d / sub).glob("*")):
                zf.write(p, f"{sub}/{p.name}")
    return {
        "zip": str(zip_path),
        "images": len(list((d / "images").glob("*.jpg"))),
        "labeled": len(list((d / "labels").glob("*.txt"))),
    }


# ---- Review canvas: per-image label read/write (correct the auto-labels) ----


def _yolo_to_boxes(text: str) -> list[list[float]]:
    """YOLO 'class cx cy w h' lines -> normalized [x1,y1,x2,y2] (canvas-friendly)."""
    boxes = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            _, cx, cy, w, h = (float(p) for p in parts)
        except ValueError:
            continue
        boxes.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
    return boxes


def image_path(name: str, file: str) -> Path | None:
    """Resolve a dataset image path, guarding against traversal."""
    safe = Path(file).name
    p = _dir(name) / "images" / safe
    return p if p.exists() else None


def list_images(name: str) -> list[str]:
    d = _dir(name) / "images"
    return [p.name for p in sorted(d.glob("*.jpg"))] if d.exists() else []


def get_labels(name: str, file: str) -> list[list[float]]:
    """Current boxes for an image as normalized [x1,y1,x2,y2] (empty if none)."""
    f = _dir(name) / "labels" / f"{Path(file).stem}.txt"
    return _yolo_to_boxes(f.read_text()) if f.exists() else []


def save_labels(name: str, file: str, boxes: list) -> dict:
    """Write corrected boxes (normalized [x1,y1,x2,y2]) as a YOLO label file."""
    if not _meta(name):
        return {"error": "unknown dataset"}
    labels = _dir(name) / "labels"
    labels.mkdir(parents=True, exist_ok=True)
    clean = [
        b for b in boxes if isinstance(b, list) and len(b) == 4 and b[2] > b[0] and b[3] > b[1]
    ]
    (labels / f"{Path(file).stem}.txt").write_text(_to_yolo(clean))
    return {"saved": len(clean), "file": Path(file).name}


# ---- Import + run a trained detector (Minder-side, on-device, on-demand) ----
# The off-box-trained .pt runs HERE (ultralytics, CPU), NOT as a Frigate detector:
# Frigate runs one model and replacing it would lose person/car/dog/cat. So a custom
# detector is a Minder detection source — keeping the stock Frigate model intact.

_MODELS: dict = {}  # slug -> loaded YOLO model (cached)
_MODELS_LOCK = threading.Lock()


def model_path(name: str) -> Path:
    return _dir(name) / "model.pt"


def has_model(name: str) -> bool:
    return model_path(name).exists()


def import_model(name: str, data: bytes) -> dict:
    """Store an off-box-trained .pt for a dataset (Studio -> Deploy)."""
    if not _meta(name):
        return {"error": "unknown dataset"}
    if not data or len(data) < 1000:
        return {"error": "that doesn't look like a model file"}
    model_path(name).write_bytes(data)
    meta = _meta(name)
    meta["model"] = "model.pt"
    meta["model_ts"] = time.time()
    (_dir(name) / "meta.json").write_text(json.dumps(meta, indent=2))
    _MODELS.pop(_slug(name), None)  # evict any cached old model
    return {"imported": True, "bytes": len(data)}


def _load_model(name: str):
    slug = _slug(name)
    if slug not in _MODELS:
        with _MODELS_LOCK:
            if slug not in _MODELS:
                from ultralytics import YOLO

                _MODELS[slug] = YOLO(str(model_path(name)))
    return _MODELS[slug]


def detect_custom(name: str, image_bytes: bytes, conf: float = 0.4) -> dict:
    """Run the dataset's trained detector on an image (CPU). Returns normalized
    [x1,y1,x2,y2] boxes + count. Graceful dict on any error / missing model."""
    if not has_model(name):
        return {"error": "no trained model imported", "boxes": [], "count": 0}
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        res = _load_model(name).predict(img, verbose=False, conf=conf, device="cpu")[0]
        raw = res.boxes.xyxyn.tolist() if res.boxes is not None else []
        boxes = [[round(float(v), 5) for v in b] for b in raw]
        return {"boxes": boxes, "count": len(boxes)}
    except Exception as e:
        return {"error": str(e)[:160], "boxes": [], "count": 0}
