"""Standalone tests for the custom-detector dataset pipeline (Scenario Studio).

Pins: dataset create, frame capture (injected grab), VLM box parsing + YOLO conversion,
auto-label (injected box fn), the no-key graceful no-op, and the YOLO export (data.yaml
+ train.py + zip). The cloud call itself is injected, so this runs with no API key.

Run in-container:  docker compose exec -T minder python /app/webapp/test_dataset.py
"""

import tempfile
import zipfile
from pathlib import Path

import dataset as ds


def _tmp():
    ds.DATASETS_DIR = Path(tempfile.mkdtemp()) / "datasets"  # type: ignore


def test_create_and_capture():
    _tmp()
    meta = ds.create_dataset("Belt Boxes", "cardboard box", "box", "Porch-1")
    assert meta["slug"] == "belt_boxes" and meta["class_name"] == "box"
    res = ds.capture_frames("Belt Boxes", count=3, _grab=lambda cam: b"x" * 2000)
    assert res["captured"] == 3 and res["total_images"] == 3
    # a too-small / empty grab is skipped
    res2 = ds.capture_frames("Belt Boxes", count=2, _grab=lambda cam: b"")
    assert res2["captured"] == 0 and res2["total_images"] == 3
    print("ok  create_dataset + capture_frames (skips empty grabs)")


def test_parse_boxes_and_yolo():
    boxes = ds._parse_boxes(
        'here you go: [{"box":[0.1,0.2,0.5,0.6]},{"box":[0.0,0.0,0.2,0.2]}] done'
    )
    assert boxes == [[0.1, 0.2, 0.5, 0.6], [0.0, 0.0, 0.2, 0.2]], boxes
    # malformed / degenerate boxes are dropped
    assert ds._parse_boxes("not json") == []
    assert ds._parse_boxes('[{"box":[0.5,0.5,0.4,0.4]}]') == []  # x2<x1
    yolo = ds._to_yolo([[0.1, 0.2, 0.5, 0.6]])
    assert yolo == "0 0.300000 0.400000 0.400000 0.400000", yolo
    print("ok  VLM box parsing (tolerant + clamped) + YOLO conversion")


def test_autolabel_injected():
    _tmp()
    ds.create_dataset("d", "box", "box", "Porch-1")
    ds.capture_frames("d", count=2, _grab=lambda cam: b"x" * 2000)
    res = ds.autolabel("d", _box_fn=lambda b64: [[0.2, 0.2, 0.8, 0.8]])
    assert res["labeled"] == 2, res
    labels = list((ds._dir("d") / "labels").glob("*.txt"))
    assert len(labels) == 2 and labels[0].read_text().startswith("0 ")
    print("ok  autolabel writes one YOLO label file per image")


def test_autolabel_no_key_noop():
    _tmp()
    ds.OPENROUTER_KEY = ""  # type: ignore
    ds.create_dataset("d", "box", "box", "Porch-1")
    ds.capture_frames("d", count=1, _grab=lambda cam: b"x" * 2000)
    res = ds.autolabel("d")  # no key, no injected fn
    assert res.get("labeled") == 0 and "no OpenRouter key" in res.get("error", "")
    print("ok  auto-label is a graceful no-op without an OpenRouter key")


def test_export_yolo_zip():
    _tmp()
    ds.create_dataset("Belt", "box", "box", "Porch-1")
    ds.capture_frames("Belt", count=2, _grab=lambda cam: b"x" * 2000)
    ds.autolabel("Belt", _box_fn=lambda b64: [[0.2, 0.2, 0.8, 0.8]])
    res = ds.export("Belt")
    assert res["images"] == 2 and res["labeled"] == 2
    d = ds._dir("Belt")
    assert (d / "data.yaml").read_text().strip().endswith("0: box")
    assert "ultralytics" in (d / "train.py").read_text()
    with zipfile.ZipFile(res["zip"]) as zf:
        names = zf.namelist()
    assert "data.yaml" in names and "train.py" in names
    assert any(n.startswith("images/") for n in names) and any(
        n.startswith("labels/") for n in names
    )
    print("ok  export writes data.yaml + train.py + a zip with images/ and labels/")


def test_list_datasets():
    _tmp()
    ds.create_dataset("one", "box", "box", "Porch-1")
    ds.capture_frames("one", count=2, _grab=lambda cam: b"x" * 2000)
    lst = ds.list_datasets()
    assert len(lst) == 1 and lst[0]["images"] == 2 and lst[0]["labeled"] == 0
    print("ok  list_datasets reports image/label counts")


def test_label_roundtrip():
    _tmp()
    ds.create_dataset("rev", "box", "box", "Porch-1")
    ds.capture_frames("rev", count=1, _grab=lambda cam: b"x" * 2000)
    file = ds.list_images("rev")[0]
    # no labels yet
    assert ds.get_labels("rev", file) == []
    # save corrected boxes (normalized x1y1x2y2) -> persisted as YOLO -> read back
    res = ds.save_labels("rev", file, [[0.1, 0.2, 0.5, 0.6], [0.0, 0.0, 0.2, 0.2]])
    assert res["saved"] == 2, res
    back = ds.get_labels("rev", file)
    # round-trips through YOLO cx/cy/w/h, so compare within float tolerance
    flat = [round(v, 4) for box in back for v in box]
    assert flat == [0.1, 0.2, 0.5, 0.6, 0.0, 0.0, 0.2, 0.2], back
    # degenerate boxes are dropped on save
    assert ds.save_labels("rev", file, [[0.5, 0.5, 0.4, 0.4]])["saved"] == 0
    assert ds.get_labels("rev", file) == []
    print("ok  per-image label save/read round-trips (YOLO <-> x1y1x2y2), drops degenerate")


def test_image_path_guards_traversal():
    _tmp()
    ds.create_dataset("g", "box", "box", "Porch-1")
    ds.capture_frames("g", count=1, _grab=lambda cam: b"x" * 2000)
    assert ds.image_path("g", "../../etc/passwd") is None  # traversal stripped -> not found
    assert ds.image_path("g", ds.list_images("g")[0]) is not None
    print("ok  image_path resolves real frames + blocks path traversal")


if __name__ == "__main__":
    test_create_and_capture()
    test_parse_boxes_and_yolo()
    test_autolabel_injected()
    test_autolabel_no_key_noop()
    test_export_yolo_zip()
    test_list_datasets()
    test_label_roundtrip()
    test_image_path_guards_traversal()
    print("\nALL DATASET TESTS PASSED")
