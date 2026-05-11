"""Workspace environment configuration — property interpolation engine.

Loads workspace.env.yaml (or workspace.env.{SWARMKIT_ENV}.yaml) and
resolves ${property.path} references in workspace.yaml values.

Two-phase resolution:
  1. Load env file → flat property map
  2. Resolve ${ENV_VAR} in property values from OS environment
  3. Resolve ${property.path} in workspace.yaml from the property map

Backward compatible: workspaces without env files work unchanged.
Property references (${...}) are only resolved if present.

See design/details/workspace-env-config.md.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_PROPERTY_PATTERN = re.compile(r"\$\{([^}]+)\}")


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


def _resolve_env_vars(value: str) -> str:
    """Resolve ${ENV_VAR} references from OS environment."""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name in os.environ:
            return os.environ[var_name]
        return match.group(0)

    return _PROPERTY_PATTERN.sub(_replace, value)


def _substitute_properties(value: str, properties: dict[str, str]) -> str:
    """Substitute ${property.path} references from the property map."""
    if "${" not in value:
        return value

    def _replace(match: re.Match[str]) -> str:
        prop_path = match.group(1)
        if prop_path in properties:
            return properties[prop_path]
        return match.group(0)

    return _PROPERTY_PATTERN.sub(_replace, value)
