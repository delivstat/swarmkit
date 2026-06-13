"""Atomic file writes — prevents corruption from partial writes / power cuts.

A plain ``path.write_text(json.dumps(...))`` can leave a truncated, unparseable
file if the process dies or the box loses power mid-write (a real risk for a
24/7 appliance). Write to a temp file, fsync, then atomically rename over the
target — readers only ever see a complete file. Stdlib-only so both the webapp
and the MCP servers can import it.
"""

import json
import os
from pathlib import Path
from typing import Any


def write_text_atomic(path: str | os.PathLike, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # atomic on POSIX


def write_json_atomic(path: str | os.PathLike, data: Any, indent: int = 2) -> None:
    write_text_atomic(path, json.dumps(data, indent=indent))


def read_json_safe(path: str | os.PathLike, default: Any = None) -> Any:
    """Read JSON, returning ``default`` on missing/corrupt — never raises. The
    defensive counterpart to the atomic writers."""
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return default
