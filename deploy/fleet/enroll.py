#!/usr/bin/env python3
"""One-shot fleet enrollment for the docker-compose fleet (design 19-22).

Runs inside the `enroll` service (the runtime image) once the panel + instances are healthy. For
each instance it: registers it in the panel's registry, has the owner mint a one-time enrollment
token with the CLI (`swarmkit fleet enroll-token`), and completes the register handshake — the panel
proves the instance holds the self-certifying fleet identity, then pins an encrypted membership.

Config via env (so the compose owns the instance list, not this script):
  PANEL_URL         panel base URL, e.g. http://panel:8800
  FLEET_INSTANCES   comma-separated `name=service-host:workspace-mount:scope`, e.g.
                    "hello=instance-hello:/workspaces/hello:manage"

The CLI mints tokens against `{workspace-mount}/.swarmkit/fleet.sqlite` — the SAME file the instance
serves (both bind-mount the one host workspace dir), which is how the two containers agree on one
fleet identity without a shared socket. Idempotent: skips instances already registered by name.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

PANEL = os.environ["PANEL_URL"].rstrip("/")


def _api(method: str, path: str, body: dict | None = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        PANEL + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:  # trusted in-network panel
        return json.loads(resp.read() or "{}")


def _mint_token(workspace: str, scope: str) -> str:
    out = subprocess.run(
        ["swarmkit", "fleet", "enroll-token", workspace, "--scope", scope],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return next(line for line in out.splitlines() if line and not line.startswith("#"))


def _list_instances() -> list[dict]:
    raw = _api("GET", "/instances")
    items = raw.get("instances", []) if isinstance(raw, dict) else raw
    return [i for i in items if isinstance(i, dict)]


def _existing_by_name(name: str) -> dict | None:
    return next((i for i in _list_instances() if i.get("name") == name), None)


def main() -> int:
    spec = os.environ.get("FLEET_INSTANCES", "").strip()
    if not spec:
        print("FLEET_INSTANCES is empty — nothing to enroll.")
        return 0

    identity = _api("GET", "/fleet/identity")
    print(f"Panel fleet identity: {identity.get('fleet_id', '?')}\n")

    failures = 0
    for raw_entry in spec.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        name, rest = entry.split("=", 1)
        host, workspace, scope = rest.split(":", 2)
        endpoint = f"http://{host}:8000"

        try:
            existing = _existing_by_name(name)
            if existing and existing.get("membership_id"):
                print(f"● {name}: already enrolled (id={existing.get('id')}) — skipping")
                continue

            iid = (
                existing["id"]
                if existing
                else _api(
                    "POST",
                    "/instances",
                    {"name": name, "endpoint": endpoint, "connection": "direct", "tier": "admin"},
                )["id"]
            )
            token = _mint_token(workspace, scope)
            reg = _api("POST", f"/instances/{iid}/register", {"enroll_token": token})
            print(
                f"● {name} ({scope}) id={iid} → membership {reg.get('membership_id')}, "
                f"cached {reg.get('counts')}"
            )
        except (urllib.error.URLError, subprocess.CalledProcessError, KeyError, ValueError) as exc:
            failures += 1
            detail = getattr(exc, "stderr", "") or str(exc)
            print(f"✗ {name}: enrollment failed — {detail}", file=sys.stderr)

    if failures:
        print(f"\n{failures} instance(s) failed to enroll.", file=sys.stderr)
        return 1
    print("\nEnrollment complete — every instance is identity-pinned with an encrypted membership.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
