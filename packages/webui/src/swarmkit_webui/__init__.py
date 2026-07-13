"""The SwarmKit web portal, as a static build served by ``swarmkit serve`` (serve-hosted-webui.md).

This package carries only data — the exported Next.js SPA under ``_static/``. The runtime imports it
optionally (the ``swarmkit-runtime[ui]`` extra) and mounts :func:`static_dir` if present.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

__version__ = "0.1.0"


def static_dir() -> Path | None:
    """Path to the bundled static portal, or ``None`` when the assets were not built into this
    install (an empty ``_static/`` ships in source; ``just build-webui`` fills it at release)."""
    try:
        path = Path(str(files("swarmkit_webui"))) / "_static"
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    return path if (path / "index.html").is_file() else None


__all__ = ["static_dir"]
