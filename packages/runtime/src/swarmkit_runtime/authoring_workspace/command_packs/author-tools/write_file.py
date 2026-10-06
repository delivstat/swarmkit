#!/usr/bin/env python3
"""Author tool: write YAML files into the target workspace.

The authoring agent calls this once per set of files it wants to land. Target workspace
path comes from the ``SWARMKIT_AUTHOR_TARGET_WORKSPACE`` environment variable (set by
the CLI shim / serve invocation) — never from the LLM, so the model cannot write to an
arbitrary directory on disk.

Allowed roots under the target workspace are fixed:

    workspace.yaml
    topologies/*.yaml
    archetypes/*.yaml
    skills/*.yaml
    funnels/*.yaml
    schemas/*.schema.json
    policies/*.yaml

Any path escaping those roots (``..``, absolute paths, paths outside the target) is
refused with ``exit code 3 / write_scope_violation``. This is the IAM-scope enforcement
promised in design/details/author-bundled-workspace.md (#1045). The stricter
``authoring.iam.write_file_scope`` operator config lands in a follow-up PR.

Stdin JSON (matches skills/write-file.yaml inputs):

    {
      "files": {
        "topologies/my-topology.yaml": "<yaml content>",
        "skills/my-skill.yaml": "<yaml content>"
      }
    }

Stdout JSON:

    {
      "written": ["topologies/my-topology.yaml", "skills/my-skill.yaml"],
      "target_workspace": "/abs/path/to/user-workspace"
    }

Exit codes:
    0 — all files written
    2 — invalid input (missing files, empty content)
    3 — path outside allowed roots (write_scope_violation)
    4 — SWARMKIT_AUTHOR_TARGET_WORKSPACE not set or does not exist
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NoReturn

#: Path prefixes (relative to the target workspace) the author may write to.
#: Anything else is refused.
_ALLOWED_PREFIXES: tuple[str, ...] = (
    "workspace.yaml",
    "topologies/",
    "archetypes/",
    "skills/",
    "funnels/",
    "schemas/",
    "policies/",
)


def _die(code: int, error: str, detail: str = "") -> NoReturn:
    sys.stdout.write(json.dumps({"error": error, "detail": detail}) + "\n")
    sys.exit(code)


def _resolve_target_workspace() -> Path:
    raw = os.environ.get("SWARMKIT_AUTHOR_TARGET_WORKSPACE", "").strip()
    if not raw:
        _die(
            4,
            "target_workspace_unset",
            "SWARMKIT_AUTHOR_TARGET_WORKSPACE must be set by the authoring invoker.",
        )
    target = Path(raw).resolve()
    if not target.is_dir():
        _die(
            4,
            "target_workspace_missing",
            f"target workspace is not a directory: {target}",
        )
    return target


def _check_scope(target: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` under ``target`` and refuse anything outside allowed roots.

    This is the one place the author can touch disk — the check runs before any write,
    so a bad model that tries to escape (``..``, absolute paths, ``~``) hits a hard stop
    before anything lands.
    """
    normalised = rel_path.strip()
    # Common LLM mistake: a leading "/" that the model treats as "workspace-rooted".
    # Normalise rather than refuse — the file still has to live under a known root,
    # which the prefix check below enforces. A path with ANY further slashes-and-
    # dots pattern (``/..``, ``~``, multi-leading-slash past normalisation) still
    # gets rejected.
    if normalised.startswith("/") and not normalised.startswith("//"):
        normalised = normalised.lstrip("/")
    if normalised.startswith(("/", "~")):
        _die(3, "write_scope_violation", f"absolute path refused: {rel_path!r}")
    if normalised in ("", ".", "..") or ".." in Path(normalised).parts:
        _die(3, "write_scope_violation", f"path escapes workspace: {rel_path!r}")
    if not any(normalised == pfx or normalised.startswith(pfx) for pfx in _ALLOWED_PREFIXES):
        _die(
            3,
            "write_scope_violation",
            f"path {rel_path!r} is outside the author's allowed roots: "
            f"{', '.join(_ALLOWED_PREFIXES)}",
        )
    candidate = (target / normalised).resolve()
    # Belt-and-braces: after resolving (symlinks, etc.), the final path must still live
    # under the target. Catches cases the string-level check above misses.
    try:
        candidate.relative_to(target)
    except ValueError:
        _die(3, "write_scope_violation", f"resolved path escapes target: {candidate}")
    return candidate


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _die(2, "invalid_json", str(exc))

    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict) or not files:
        _die(2, "no_files", 'input must be {"files": {relpath: content, ...}}')

    target = _resolve_target_workspace()

    written: list[str] = []
    for rel_path, content in files.items():
        if not isinstance(rel_path, str) or not isinstance(content, str):
            _die(2, "invalid_entry", f"non-string entry for {rel_path!r}")
        if not content.strip():
            _die(2, "empty_content", f"content for {rel_path!r} is empty")
        dest = _check_scope(target, rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(rel_path)

    sys.stdout.write(json.dumps({"written": written, "target_workspace": str(target)}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
