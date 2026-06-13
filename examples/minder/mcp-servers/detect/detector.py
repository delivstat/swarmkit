"""Shared YOLO object detector for Minder.

Purpose-built object detection for 24/7 local monitoring — far more reliable
than a VLM at "is there a person / car / animal", and fast enough to run on
CPU (no GPU contention). Imported directly by the camera server's monitoring
loop and wrapped as an MCP tool by the detect server.

Runs CPU-only inside the container (no CUDA passthrough), so torch never
touches the GPU.
"""

from __future__ import annotations

import os
import threading

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("YOLO_VERBOSE", "False")

# COCO class groups for the conditions home monitoring cares about.
PERSON_CLASSES = {0}
VEHICLE_CLASSES = {1, 2, 3, 5, 7}  # bicycle, car, motorcycle, bus, truck
ANIMAL_CLASSES = {14, 15, 16, 17, 18, 19, 20, 21, 22, 23}  # bird..giraffe

_CLASS_NAME = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    14: "bird",
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
}

DEFAULT_CONF = float(os.environ.get("MINDER_DETECT_CONF", "0.35"))
MODEL_PATH = os.environ.get("MINDER_YOLO_MODEL", "/app/models/yolov8n.pt")

_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from ultralytics import YOLO

                _model = YOLO(MODEL_PATH)
    return _model


def classes_for_condition(condition: str) -> set[int]:
    """Map a natural-language condition to the COCO classes to look for.
    Returns an empty set if the condition isn't about a detectable object
    (caller should fall back to the VLM)."""
    t = (condition or "").lower()
    classes: set[int] = set()
    if any(
        w in t
        for w in [
            "person",
            "people",
            "someone",
            "somebody",
            "anyone",
            "human",
            "intruder",
            "visitor",
            "man",
            "woman",
            "child",
        ]
    ):
        classes |= PERSON_CLASSES
    if any(
        w in t for w in ["car", "vehicle", "truck", "bike", "bicycle", "motorcycle", "van", "bus"]
    ):
        classes |= VEHICLE_CLASSES
    if any(w in t for w in ["animal", "dog", "cat", "bird", "cow", "monkey", "pet"]):
        classes |= ANIMAL_CLASSES
    return classes


def detect(
    image_path: str, want_classes: set[int] | None = None, conf: float = DEFAULT_CONF
) -> dict:
    """Run detection on an image. Returns counts per class name plus the
    detections matching want_classes (if given)."""
    model = _get_model()
    result = model.predict(image_path, verbose=False, conf=conf, device="cpu")[0]

    counts: dict[str, int] = {}
    matched = 0
    best_conf = 0.0
    if result.boxes is not None:
        clss = [int(c) for c in result.boxes.cls.tolist()]
        confs = [float(c) for c in result.boxes.conf.tolist()]
        for cid, cf in zip(clss, confs):
            name = _CLASS_NAME.get(cid, str(cid))
            counts[name] = counts.get(name, 0) + 1
            if want_classes and cid in want_classes:
                matched += 1
                best_conf = max(best_conf, cf)

    return {
        "counts": counts,
        "matched": matched,
        "best_confidence": round(best_conf, 2),
    }


def describe_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "nothing detected"
    return ", ".join(f"{n} {name}{'s' if n > 1 else ''}" for name, n in counts.items())
