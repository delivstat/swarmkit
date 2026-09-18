"""The mechanics behind ``swarmkit upgrade`` (upgrade-command.md).

Pure, injectable pieces — detection, PyPI lookup, command construction — so the CLI stays thin and
the behaviour is testable without a network or a real install. `swarmkit upgrade` upgrades the local
install only; the fleet is the control plane's job and the runtime never depends on it.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from collections.abc import Callable

_PYPI_URL = "https://pypi.org/pypi/swarmkit-runtime/json"

#: (import name, extra name) — an extra is "on" when its import is available. Order is the order
#: they are re-applied to the install spec.
_EXTRA_IMPORTS: tuple[tuple[str, str], ...] = (
    ("swarmkit_webui", "ui"),
    ("psycopg", "postgres"),
    ("anthropic", "anthropic"),
    ("openai", "openai"),
    ("google.genai", "google"),
)


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect_extras(importable: Callable[[str], bool] = _importable) -> tuple[str, ...]:
    """The extras the current install has, reconstructed from what is importable — so an upgrade
    re-installs the same surface (``[ui,postgres]``) rather than a bare package."""
    return tuple(extra for mod, extra in _EXTRA_IMPORTS if importable(mod))


def detect_method(executable: str | None = None, *, in_container: bool | None = None) -> str:
    """How the runtime was installed, from where its interpreter lives. Docker and anything
    unrecognised are named so the command refuses to drive them rather than guessing."""
    if os.path.exists("/.dockerenv") if in_container is None else in_container:
        return "docker"
    path = (executable or sys.executable).replace("\\", "/")
    if "/uv/tools/" in path:
        return "uv-tool"
    if "/pipx/" in path or "/pipx/venvs/" in path:
        return "pipx"
    if os.environ.get("VIRTUAL_ENV") or ("/venv/" in path or path.endswith("/.venv/bin/python")):
        return "pip"
    return "pip" if _looks_like_venv(path) else "unknown"


def _looks_like_venv(path: str) -> bool:
    # A pip install we can drive lives in *some* environment with a pip; the system python is the
    # one case we do not want to `pip install -U` into. Treat a non-system prefix as a venv.
    return sys.prefix != sys.base_prefix


def package_spec(extras: tuple[str, ...], version: str | None = None) -> str:
    base = "swarmkit-runtime" + (f"[{','.join(extras)}]" if extras else "")
    return base + (f"=={version}" if version else "")


def upgrade_command(
    method: str, extras: tuple[str, ...], version: str | None = None
) -> list[str] | None:
    """The argv for this method, or None for one we do not drive (docker/unknown)."""
    spec = package_spec(extras, version)
    if method == "uv-tool":
        return ["uv", "tool", "install", "--upgrade", spec]
    if method == "pipx":
        # `--force` re-applies extras and a pin; a bare `pipx upgrade` keeps neither.
        return ["pipx", "install", "--force", spec]
    if method == "pip":
        return [sys.executable, "-m", "pip", "install", "-U", spec]
    return None


def manual_command(extras: tuple[str, ...], version: str | None = None) -> str:
    """The one-line command to print when we refuse to drive the install ourselves."""
    return f'pip install -U "{package_spec(extras, version)}"'


def version_key(version: str) -> tuple[int, ...]:
    out = []
    for part in version.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


class UpgradeError(RuntimeError):
    """The upgrade cannot proceed — offline with no target, not installed as a package, etc."""


def latest_version(transport: object | None = None) -> str:
    """The newest non-yanked ``swarmkit-runtime`` on PyPI. Raises ``UpgradeError`` when offline."""
    import httpx  # noqa: PLC0415

    try:
        with httpx.Client(timeout=15.0, transport=transport) as client:  # type: ignore[arg-type]
            resp = client.get(_PYPI_URL)
    except httpx.HTTPError as exc:
        raise UpgradeError(
            f"could not reach PyPI to find the newest version ({exc}); pass --to X.Y.Z to upgrade "
            "to a specific version"
        ) from exc
    if resp.status_code != 200:
        raise UpgradeError(f"PyPI answered {resp.status_code} for swarmkit-runtime")
    data = resp.json()
    latest = str(data.get("info", {}).get("version") or "")
    if not latest:
        raise UpgradeError("PyPI returned no version for swarmkit-runtime")
    return latest


def run_command(command: list[str]) -> int:
    """Run the install command, streaming its output. Injected in tests."""
    return subprocess.run(command, check=False).returncode
