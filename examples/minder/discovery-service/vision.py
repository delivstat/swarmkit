"""Minder Vision Service — analyses camera snapshots using a local vision model.

Feeds camera snapshots to a vision LLM (Gemma 4 E2B via Ollama) and returns
structured observations: what's in the frame, whether it matches a trigger condition.
"""

import base64
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

OLLAMA_URL = "http://localhost:11434"
VISION_MODEL = "gemma4:e2b"
SNAPSHOT_DIR = Path("/data/snapshots")


@dataclass
class VisionResult:
    camera_ip: str
    camera_name: str
    query: str
    answer: str
    match: bool
    latency_ms: int
    timestamp: str


def analyse_frame(image_path: Path, query: str, model: str = VISION_MODEL) -> dict:
    """Send a single image + query to the vision model and return the response."""
    image_b64 = base64.b64encode(image_path.read_bytes()).decode()

    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": query,
                    "images": [image_b64],
                }
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 200,
            },
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

    content = result.get("message", {}).get("content", "")

    return {
        "response": content,
        "latency_ms": elapsed_ms,
    }


def check_scene(image_path: Path, condition: str, model: str = VISION_MODEL) -> VisionResult:
    """Check whether a condition is met in a camera frame.

    Returns a VisionResult with match=True/False based on the model's assessment.
    """
    query = (
        f"Look at this security camera image carefully. "
        f"Answer this question with YES or NO first, then explain briefly: {condition}"
    )

    result = analyse_frame(image_path, query, model)
    response = result["response"].strip()

    match = response.upper().startswith("YES")

    ip_from_filename = image_path.stem.replace("_", ".")

    return VisionResult(
        camera_ip=ip_from_filename,
        camera_name=image_path.stem,
        query=condition,
        answer=response,
        match=match,
        latency_ms=result["latency_ms"],
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def describe_scene(image_path: Path, model: str = VISION_MODEL) -> VisionResult:
    """Get a general description of what the camera sees."""
    query = (
        "You are a home security camera AI assistant. "
        "Describe what you see in this security camera image in 2-3 sentences. "
        "Focus on: people present (how many, what they're doing), vehicles, animals, "
        "anything unusual or noteworthy. If the scene is empty, say so."
    )

    result = analyse_frame(image_path, query, model)

    return VisionResult(
        camera_ip=image_path.stem.replace("_", "."),
        camera_name=image_path.stem,
        query="describe scene",
        answer=result["response"].strip(),
        match=False,
        latency_ms=result["latency_ms"],
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def scan_all_cameras(condition: str | None = None, model: str = VISION_MODEL) -> list[VisionResult]:
    """Scan all camera snapshots in the data directory."""
    snapshots = sorted(SNAPSHOT_DIR.glob("*.jpg"))
    if not snapshots:
        print("No snapshots found in /data/snapshots/")
        return []

    results = []
    for snap in snapshots:
        if condition:
            result = check_scene(snap, condition, model)
            status = "MATCH" if result.match else "no match"
            print(
                f"  {result.camera_ip:>15} — {status} ({result.latency_ms}ms): {result.answer[:100]}"
            )
        else:
            result = describe_scene(snap, model)
            print(f"  {result.camera_ip:>15} ({result.latency_ms}ms): {result.answer[:120]}")
        results.append(result)

    return results


if __name__ == "__main__":
    import sys

    model = VISION_MODEL
    condition = None

    args = sys.argv[1:]
    if args and args[0] == "--model":
        model = args[1]
        args = args[2:]

    if args:
        condition = " ".join(args)
        print(f'\nChecking all cameras: "{condition}" (model: {model})\n')
    else:
        print(f"\nDescribing all camera scenes (model: {model})\n")

    results = scan_all_cameras(condition, model)

    # Save results
    output = [
        {
            "camera_ip": r.camera_ip,
            "camera_name": r.camera_name,
            "query": r.query,
            "answer": r.answer,
            "match": r.match,
            "latency_ms": r.latency_ms,
            "timestamp": r.timestamp,
        }
        for r in results
    ]
    outfile = Path("/data/vision_results.json")
    outfile.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {outfile}")

    matches = [r for r in results if r.match]
    if condition and matches:
        print(
            f"\n*** {len(matches)} camera(s) matched: {', '.join(r.camera_ip for r in matches)} ***"
        )
