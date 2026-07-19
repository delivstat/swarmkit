"""Workspace environment configuration — property + env interpolation engine.

Resolves ``${...}`` references in artifact values, in this order per ``${NAME}``:
  1. the workspace property map (dotted paths from workspace.env.yaml, if present),
  2. the OS environment (``${ENV_VAR}``),
  3. a ``:-default`` when written ``${NAME:-default}``.
An unresolved ref with no default is left literal; ``$${NAME}`` escapes to a literal
``${NAME}``. So an artifact is env-configurable with or without an env file.

See design/details/workspace-env-config.md and design/details/artifact-env-substitution.md.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml


def load_env_config(workspace_root: Path) -> dict[str, str]:
    """Load and resolve the workspace env config.

    Resolution order:
      1. workspace.env.{SWARMKIT_ENV}.yaml (if SWARMKIT_ENV is set)
      2. workspace.env.yaml (default)
      3. ${ENV_VAR} in property values resolved from OS environment

    Returns a flat map of dotted property paths to resolved values.
    """
    env_name = os.environ.get("SWARMKIT_ENV", "")

    env_file: Path | None = None
    if env_name:
        candidate = workspace_root / f"workspace.env.{env_name}.yaml"
        if candidate.is_file():
            env_file = candidate

    if env_file is None:
        default = workspace_root / "workspace.env.yaml"
        if default.is_file():
            env_file = default

    if env_file is None:
        return {}

    try:
        raw = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}

    if not isinstance(raw, dict):
        return {}

    flat = _flatten(raw)

    resolved: dict[str, str] = {}
    for key, value in flat.items():
        resolved[key] = _resolve_env_vars(str(value))

    return resolved


def interpolate_value(value: Any, properties: dict[str, str]) -> Any:
    """Resolve ${property.path} references in a value.

    - Strings with ${...} get property substitution
    - Dicts and lists are traversed recursively
    - Non-string values pass through unchanged
    """
    if isinstance(value, str):
        return _substitute_properties(value, properties)
    if isinstance(value, dict):
        return {k: interpolate_value(v, properties) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_value(item, properties) for item in value]
    return value


def interpolate_dict(data: dict[str, Any], properties: dict[str, str]) -> dict[str, Any]:
    """Resolve all ${property.path} references in a dict tree."""
    return {k: interpolate_value(v, properties) for k, v in data.items()}


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict to dotted key paths."""
    result: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            result.update(_flatten(value, full_key))
        else:
            result[full_key] = str(value)
    return result


# Matches ${NAME} or ${NAME:-default}; a leading $ ($${NAME}) escapes to a literal ${NAME}.
_REF_PATTERN = re.compile(r"(\$?)\$\{([^}]+)\}")


def _resolve_refs(value: str, properties: dict[str, str] | None = None) -> str:
    """Resolve ``${NAME}`` references in a string.

    Resolution order for ``${NAME}``: the workspace property map (dotted paths from
    workspace.env.yaml), then the OS environment, then a ``:-default`` when written
    ``${NAME:-default}``. An unresolved ref with no default is left literal (backward
    compatible). ``$${NAME}`` is an escape yielding a literal ``${NAME}``.
    """
    if "${" not in value:
        return value
    props = properties or {}

    def _replace(m: re.Match[str]) -> str:
        if m.group(1) == "$":  # $${...} -> literal ${...}
            return "${" + m.group(2) + "}"
        name, sep, default = m.group(2).partition(":-")
        if name in props:
            return props[name]
        if name in os.environ:
            return os.environ[name]
        if sep:  # ${NAME:-default}
            return default
        return "${" + m.group(2) + "}"  # unresolved, no default -> leave literal

    return _REF_PATTERN.sub(_replace, value)


def _resolve_env_vars(value: str) -> str:
    """Resolve ${ENV_VAR} (and ${VAR:-default}) references from the OS environment."""
    return _resolve_refs(value)


def _substitute_properties(value: str, properties: dict[str, str]) -> str:
    """Resolve ${property.path} + ${ENV_VAR} + ${VAR:-default} references for an artifact."""
    return _resolve_refs(value, properties)
