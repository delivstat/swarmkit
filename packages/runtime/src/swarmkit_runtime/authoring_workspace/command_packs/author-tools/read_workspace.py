#!/usr/bin/env python3
"""Author tool: summarise the target workspace's current contents.

Returns the workspace.yaml text + a per-kind inventory of top-level YAMLs (topologies,
archetypes, skills, funnels). The authoring agent calls this at the start of a session
to avoid proposing a skill/topology/archetype that already exists.

Target workspace path comes from ``SWARMKIT_AUTHOR_TARGET_WORKSPACE``.

Stdin JSON: ``{}`` (no inputs).

Stdout JSON:

    {
      "target_workspace": "/abs/path",
      "workspace_yaml": "<content or empty>",
      "topologies": ["name1", "name2"],
      "archetypes": [...],
      "skills": [...],
      "funnels": [...]
    }

Exit codes:
    0 — summary written
    4 — SWARMKIT_AUTHOR_TARGET_WORKSPACE not set or does not exist
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NoReturn


def _die(code: int, error: str, detail: str = "") -> NoReturn:
    sys.stdout.write(json.dumps({"error": error, "detail": detail}) + "\n")
    sys.exit(code)


def _resolve_target_workspace() -> Path:
    raw = os.environ.get("SWARMKIT_AUTHOR_TARGET_WORKSPACE", "").strip()
    if not raw:
        _die(4, "target_workspace_unset", "SWARMKIT_AUTHOR_TARGET_WORKSPACE must be set.")
    target = Path(raw).resolve()
    if not target.is_dir():
        _die(4, "target_workspace_missing", f"not a directory: {target}")
    return target


def _list_yaml_stems(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    return sorted(f.stem for f in path.glob("*.yaml"))


def main() -> int:
    target = _resolve_target_workspace()
    ws_yaml = target / "workspace.yaml"
    workspace_yaml = ws_yaml.read_text(encoding="utf-8") if ws_yaml.is_file() else ""

    out = {
        "target_workspace": str(target),
        "workspace_yaml": workspace_yaml,
        "topologies": _list_yaml_stems(target / "topologies"),
        "archetypes": _list_yaml_stems(target / "archetypes"),
        "skills": _list_yaml_stems(target / "skills"),
        "funnels": _list_yaml_stems(target / "funnels"),
    }
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
