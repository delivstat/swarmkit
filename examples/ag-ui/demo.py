"""Minimal AG-UI client. Posts a `RunAgentInput` to `POST /api/ag-ui/run` and prints each
SSE event as it arrives. No AG-UI SDK — just httpx + manual frame parse — to show the wire
protocol has nothing hidden in it.

Usage:
    # terminal 1: swarmkit serve examples/hello-swarm/workspace
    uv run python examples/ag-ui/demo.py
"""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = os.environ.get("SWARMKIT_URL", "http://127.0.0.1:8000")
TOPOLOGY = os.environ.get("SWARMKIT_TOPOLOGY", "hello")


def main() -> int:
    body = {
        "threadId": "demo-thread",
        "messages": [{"role": "user", "content": "say hello"}],
        "context": {"topology": TOPOLOGY},
    }
    with (
        httpx.Client(timeout=None) as client,
        client.stream("POST", f"{BASE}/api/ag-ui/run", json=body) as res,
    ):
        if res.status_code != 200:
            print(f"HTTP {res.status_code}: {res.read().decode()}", file=sys.stderr)
            return 1
        for line in res.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            event = json.loads(line[len("data:") :].strip())
            etype = event.get("type", "?")
            if etype == "TextMessageContent":
                print(f"  [content] {event['delta']}")
            else:
                rest = {k: v for k, v in event.items() if k != "type"}
                print(f"[{etype}] {json.dumps(rest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
