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

import json
import os
import re
import sys
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

    required = ("id", "name", "role", "greeting", "icon", "topology_yaml")
    missing = [k for k in required if not spec.get(k)]
    if missing:
        _die(2, "missing_field", f"required: {', '.join(missing)}")
    dot_id = str(spec["id"]).strip()
    if not ID_RE.match(dot_id):
        _die(2, "invalid_id", f"id must match {ID_RE.pattern}")

    target = Path(os.environ.get("AUTHOR_TARGET_WORKSPACE", str(Path.cwd() / "workspace")))
    topologies_dir = target / "topologies"
    if not topologies_dir.is_dir():
        _die(4, "target_missing", f"{topologies_dir} does not exist")

    yaml_text = str(spec["topology_yaml"])
    _validate_topology_yaml(yaml_text)

    topology_path = topologies_dir / f"{dot_id}.yaml"
    topology_path.write_text(yaml_text, encoding="utf-8")

    dot_entry = {
        "id": dot_id,
        "name": spec["name"],
        "role": spec["role"],
        "greeting": spec["greeting"],
        "icon": spec["icon"],
        "topology": dot_id,
        "renderers": spec.get("renderers") or [],
    }
    sys.stdout.write(
        json.dumps(
            {
                "id": dot_id,
                "topology_path": str(topology_path),
                "dot": dot_entry,
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
