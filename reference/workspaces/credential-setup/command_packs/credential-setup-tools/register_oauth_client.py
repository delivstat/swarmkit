#!/usr/bin/env python3
"""The oauth-client-registrar's HTTP mouthpiece.

Reads a plan JSON on stdin and POSTs to the runtime's /api/oauth/clients. Shaped as a
plain script (not a module) so the `command` skill backing can invoke it as `argv`
without needing a Python package import path.

The plan carries only operator-authored data. Nothing here reaches the OAuth client's
authorization server — that's the runtime's job once the client row exists.

Stdin JSON:
    {
      "issuer":        "https://accounts.google.com",
      "client_id":     "123.apps.googleusercontent.com",
      "client_secret": "GOCSPX-…",
      "client_type":   "desktop",
      "display_name":  "Google (DOT appliance)",
      "scopes":        ["https://www.googleapis.com/auth/gmail.readonly"]
    }

Env:
    SWARMKIT_RUNTIME_URL    base URL (default http://localhost:8000)
    SWARMKIT_RUNTIME_TOKEN  optional bearer; required when the runtime has auth on

Exit codes:
    0 — client registered (prints the display row as JSON on stdout)
    2 — invalid plan (missing required fields)
    3 — runtime refused the registration (prints the 4xx body)
    4 — network error reaching the runtime
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def _runtime_url() -> str:
    return os.environ.get("SWARMKIT_RUNTIME_URL", "http://localhost:8000").rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get("SWARMKIT_RUNTIME_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _read_plan(stream: Any) -> dict[str, Any]:
    try:
        plan = json.load(stream)
    except json.JSONDecodeError as exc:
        print(f"invalid JSON on stdin: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(plan, dict):
        print("plan must be a JSON object", file=sys.stderr)
        sys.exit(2)
    required = {"client_id", "client_type"}
    missing = sorted(required - set(plan))
    if missing:
        print(f"plan missing required fields: {missing}", file=sys.stderr)
        sys.exit(2)
    if "issuer" not in plan and "endpoint" not in plan:
        print("plan must carry issuer or endpoint", file=sys.stderr)
        sys.exit(2)
    return plan


def main() -> int:
    plan = _read_plan(sys.stdin)
    req = urllib.request.Request(
        f"{_runtime_url()}/api/oauth/clients",
        data=json.dumps(plan).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        print(f"runtime refused the registration: {exc.code}", file=sys.stderr)
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 3
    except urllib.error.URLError as exc:
        print(f"could not reach the runtime at {_runtime_url()}: {exc.reason}", file=sys.stderr)
        return 4
    sys.stdout.write(body)
    if not body.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
