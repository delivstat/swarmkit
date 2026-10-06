#!/usr/bin/env python3
"""The Dot Author's write mouthpiece.

Reads a Dot spec JSON on stdin, validates the topology YAML inside against the canonical
SwarmKit topology schema, writes it into the target workspace's topologies/ directory,
and prints the Dot entry the consumer app should persist. One tool call, one commit —
nothing is written if validation fails.

Target workspace path is picked by env:
    AUTHOR_TARGET_WORKSPACE   default: $PWD/workspace

Stdin JSON (matches skills/create-dot.yaml inputs):
    {
      "id": "weekly-sweep",
      "name": "Weekly Sweep",
      "role": "Spot stale PRs + branches",
      "greeting": "Give me a repo to sweep.",
      "icon": "github",
      "topology_yaml": "apiVersion: swarmkit/v1\\nkind: Topology\\n...",
      "renderers": ["github-item"]
    }

Exit codes:
    0 — written (prints {id, topology_path, dot} JSON)
    2 — invalid spec (missing field, bad slug)
    3 — topology YAML failed schema validation
    4 — target workspace path does not exist
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-not-found]
from jsonschema.exceptions import ValidationError  # type: ignore[import-not-found]
from swarmkit_schema import validate  # type: ignore[import-not-found]

ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _die(code: int, error: str, detail: str = "") -> None:
    sys.stdout.write(json.dumps({"error": error, "detail": detail}) + "\n")
    sys.exit(code)


def _validate_topology_yaml(yaml_text: str) -> None:
    """Parse + validate the topology YAML against the canonical schema."""
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        _die(3, "yaml_parse_failed", str(exc))
        return

    try:
        validate("topology", parsed)
    except ValidationError as exc:
        _die(3, "topology_invalid", exc.message)


def main() -> int:
    raw = sys.stdin.read()
    try:
        spec: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die(2, "invalid_json", str(exc))

    required = ("id", "name", "role", "greeting", "icon", "topology")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        _die(2, "missing_field", f"required: {', '.join(missing)}")
    dot_id = str(spec["id"]).strip()
    if not ID_RE.match(dot_id):
        _die(2, "invalid_id", f"id must match {ID_RE.pattern}")
    topology_name = str(spec["topology"]).strip()
    if not ID_RE.match(topology_name):
        _die(2, "invalid_topology_id", f"topology must match {ID_RE.pattern}")

    target = Path(os.environ.get("AUTHOR_TARGET_WORKSPACE", str(Path.cwd() / "workspace")))
    topologies_dir = target / "topologies"
    if not topologies_dir.is_dir():
        _die(4, "target_missing", f"{topologies_dir} does not exist")

    # The bundled author (swarmkit:author:topology) has already written the topology
    # YAML through its own IAM-scoped write-file skill (#1045). This script no longer
    # takes YAML; it just confirms the file is there and registers the Dot. Refuse to
    # register against a missing topology — a dot that points at nothing is worse than
    # none at all.
    topology_path = topologies_dir / f"{topology_name}.yaml"
    if not topology_path.is_file():
        _die(
            3,
            "topology_missing",
            f"topology file not found at {topology_path}; author-topology must run first",
        )
    _validate_topology_yaml(topology_path.read_text(encoding="utf-8"))

    dot_entry = {
        "id": dot_id,
        "name": spec["name"],
        "role": spec["role"],
        "greeting": spec["greeting"],
        "icon": spec["icon"],
        "topology": topology_name,
        "renderers": spec.get("renderers") or [],
    }

    # Register the Dot with the consumer app (the dots-app in reference/apps/dots). The
    # script writes topology YAML into the shared workspace, but dots.local.json lives in
    # the consumer's filesystem — so we POST the Dot entry to its intake endpoint. Env:
    #   DOTS_INTAKE_URL   — target URL, e.g. http://dots-app:3500/api/dots/intake
    #   DOTS_INTAKE_TOKEN — optional shared secret; required when the consumer side sets it
    # Both env vars are set in reference/apps/dots/docker-compose.yml. When they are unset
    # the script just emits the Dot JSON on stdout (back-compat with hand-wired consumers
    # that read the stdout result themselves).
    intake_url = os.environ.get("DOTS_INTAKE_URL", "").strip()
    intake_status: str = "no_intake_configured"
    if intake_url:
        req_headers = {"content-type": "application/json"}
        token = os.environ.get("DOTS_INTAKE_TOKEN", "").strip()
        if token:
            req_headers["x-dots-intake-token"] = token
        req = urllib.request.Request(
            intake_url,
            data=json.dumps(dot_entry).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                intake_status = f"ok ({resp.status})"
        except urllib.error.HTTPError as exc:
            # Register-known-failure path. The Dot is NOT in the consumer; surface the error
            # back to the agent so it can retry or tell the user.
            detail = ""
            with contextlib.suppress(Exception):
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            _die(5, "intake_failed", f"HTTP {exc.code} from {intake_url}: {detail}")
        except (urllib.error.URLError, TimeoutError) as exc:
            _die(5, "intake_unreachable", f"{intake_url}: {exc}")

    sys.stdout.write(
        json.dumps(
            {
                "id": dot_id,
                "topology_path": str(topology_path),
                "dot": dot_entry,
                "intake": intake_status,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
