#!/usr/bin/env python3
"""Author tool: validate the target workspace.

Runs the canonical workspace resolver + schema validator against the target workspace
and returns a structured summary. The author calls this after writing files to confirm
the generated set is consistent (topology references resolve, skills referenced in
archetypes exist, workspace.yaml is well-formed).

Target workspace path comes from ``SWARMKIT_AUTHOR_TARGET_WORKSPACE``.

Stdin JSON: ``{}``.

Stdout JSON:

    {
      "target_workspace": "/abs/path",
      "valid": true,
      "counts": {"topologies": N, "archetypes": N, "skills": N, "funnels": N},
      "errors": []
    }

On validation failure ``valid`` is false and ``errors`` carries per-error
``{message, suggestion}`` entries straight from the resolver.

Exit codes:
    0 — validation ran (valid or not — read ``.valid``)
    4 — SWARMKIT_AUTHOR_TARGET_WORKSPACE not set or does not exist
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.resolver import resolve_workspace


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


def main() -> int:
    target = _resolve_target_workspace()
    try:
        ws = resolve_workspace(target)
    except ResolutionErrors as exc:
        errors = [
            {"message": err.message, "suggestion": err.suggestion or ""} for err in exc.errors
        ]
        sys.stdout.write(
            json.dumps(
                {
                    "target_workspace": str(target),
                    "valid": False,
                    "counts": {},
                    "errors": errors,
                }
            )
            + "\n"
        )
        return 0
    except FileNotFoundError as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "target_workspace": str(target),
                    "valid": False,
                    "counts": {},
                    "errors": [{"message": str(exc), "suggestion": ""}],
                }
            )
            + "\n"
        )
        return 0

    sys.stdout.write(
        json.dumps(
            {
                "target_workspace": str(target),
                "valid": True,
                "counts": {
                    "topologies": len(ws.topologies),
                    "archetypes": len(ws.archetypes),
                    "skills": len(ws.skills),
                    "funnels": len(getattr(ws, "funnels", {}) or {}),
                },
                "errors": [],
            }
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
