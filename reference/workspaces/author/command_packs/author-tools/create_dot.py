#!/usr/bin/env python3
"""Write a Dot sidebar entry for an existing topology.

The Dot Author's only tool. Takes a Dot spec from argv (SwarmKit's command runner fills
{name} placeholders in argv from the skill's input JSON; it does not pipe stdin). Checks
that the referenced topology YAML already exists in the target workspace, then prints
the Dot entry the consumer app should persist. Does NOT author topologies — that is
`swarmkit author topology`'s job, upstream.

Target workspace path is picked by env:
    AUTHOR_TARGET_WORKSPACE   default: $PWD/workspace

Positional argv (matches skills/create-dot.yaml placeholders):
    create_dot.py <id> <name> <role> <greeting> <icon> <topology> <renderers_json>

Exit codes:
    0 — written (prints {id, topology_path, dot} JSON)
    2 — invalid spec (missing field, bad slug, bad renderers JSON)
    3 — target workspace has no `topologies/<topology>.yaml` to wrap
    4 — target workspace path does not exist
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _die(code: int, error: str, detail: str = "") -> None:
    sys.stdout.write(json.dumps({"error": error, "detail": detail}) + "\n")
    sys.exit(code)


def _parse_renderers(raw: str) -> list[str]:
    """Accept either a JSON array ('[\"brief-item\"]') or a comma-separated string."""
    raw = (raw or "").strip()
    if not raw or raw == "[]":
        return []
    if raw.startswith("["):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            _die(2, "invalid_renderers", f"not valid JSON: {exc}")
            return []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            _die(2, "invalid_renderers", "renderers must be a list of strings")
            return []
        return value
    return [r.strip() for r in raw.split(",") if r.strip()]


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 6:
        _die(
            2,
            "missing_field",
            "expected 6-7 argv (id name role greeting icon topology [renderers])",
        )
    dot_id = args[0].strip()
    name = args[1].strip()
    role = args[2].strip()
    greeting = args[3].strip()
    icon = args[4].strip()
    topology = args[5].strip()
    renderers_raw = args[6] if len(args) >= 7 else "[]"

    if not ID_RE.match(dot_id):
        _die(2, "invalid_id", f"id must match {ID_RE.pattern}")
    if not (name and role and greeting and icon and topology):
        _die(2, "missing_field", "name, role, greeting, icon, topology are required")

    target = Path(os.environ.get("AUTHOR_TARGET_WORKSPACE", str(Path.cwd() / "workspace")))
    topologies_dir = target / "topologies"
    if not topologies_dir.is_dir():
        _die(4, "target_missing", f"{topologies_dir} does not exist")

    topology_path = topologies_dir / f"{topology}.yaml"
    if not topology_path.is_file():
        _die(
            3,
            "topology_missing",
            (
                f"'{topology_path}' not found. Run `swarmkit author topology {target}` to "
                "create it, then call create-dot again."
            ),
        )

    renderers = _parse_renderers(renderers_raw)
    dot_entry = {
        "id": dot_id,
        "name": name,
        "role": role,
        "greeting": greeting,
        "icon": icon,
        "topology": topology,
        "renderers": renderers,
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
