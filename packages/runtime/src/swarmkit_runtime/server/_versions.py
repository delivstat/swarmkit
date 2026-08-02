"""Component versions, read from installed package metadata.

`swarmkit serve` is the runtime hosting a *separately versioned* portal, so "what version is this"
has two answers. Reporting one of them — or worse, a hardcoded literal — is how the UI ended up
displaying `v1.2.58` long after the runtime had reached 1.129.0.

Metadata, never literals: a constant in source drifts the moment `pyproject.toml` is bumped, which
is exactly what happened to `swarmkit_webui.__version__` (still 0.1.0 at release 0.6.0).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _safe(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return ""


def runtime_version() -> str:
    """The installed ``swarmkit-runtime`` version, or empty when running uninstalled."""
    return _safe("swarmkit-runtime")


def webui_version() -> str:
    """The installed ``swarmkit-webui`` version — empty when serve runs headless (no ``[ui]``)."""
    return _safe("swarmkit-webui")


def component_versions() -> dict[str, str]:
    """Both, for the health payload the portal footer reads."""
    return {"runtime_version": runtime_version(), "webui_version": webui_version()}


__all__ = ["component_versions", "runtime_version", "webui_version"]
