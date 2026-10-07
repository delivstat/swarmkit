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

#: Reserved top-level key: a list of dotted property paths whose VALUES must never be displayed.
#: Declared rather than guessed — a name heuristic gets `db.dsn` and `webhook.callback` wrong, and
#: being wrong in that direction prints a credential to a terminal, a log, and a web page.
SECRETS_KEY = "secrets"


def load_secret_paths(workspace_root: Path) -> set[str]:
    """The property paths this workspace declares secret (the reserved ``secrets:`` list)."""
    raw = _read_env_file(workspace_root)
    declared = raw.get(SECRETS_KEY) if isinstance(raw, dict) else None
    if not isinstance(declared, list):
        return set()
    return {str(item).strip() for item in declared if str(item).strip()}


def _read_env_file(workspace_root: Path) -> dict[str, Any]:
    """The raw parsed env file (unflattened, unresolved), or ``{}``."""
    env_name = os.environ.get("SWARMKIT_ENV", "")
    candidates = []
    if env_name:
        candidates.append(workspace_root / f"workspace.env.{env_name}.yaml")
    candidates.append(workspace_root / "workspace.env.yaml")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError):
            return {}
        return raw if isinstance(raw, dict) else {}
    return {}


def load_env_config_typed(
    workspace_root: Path,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Load the workspace env config and return both string and typed views.

    - **string view** (first element): what ``load_env_config`` returns — every value
      is a ``str`` with ``${ENV_VAR}`` references resolved against the OS environment.
      Needed for existing consumers (the System page, `_env_registry`) that build
      display rows.
    - **typed view** (second element): the SAME keys, but each value is the original
      YAML-parsed scalar (int, float, bool, None, list, dict leaf). No stringification.
      Used by :func:`interpolate_value` to return a typed result when an artifact's
      value is a whole-string reference, so e.g. ``num_ctx: ${model.vision.num_ctx}``
      arrives at the provider as ``4096`` rather than ``"4096"`` (#879).

    The two views have the same keys. The string view only resolves ``${ENV_VAR}``
    inside its OWN values — the typed view carries the raw scalar untouched; env
    substitution still happens in the interpolation layer when a leaf is a string.
    """
    raw = _read_env_file(workspace_root)
    if not raw:
        return {}, {}
    raw = {k: v for k, v in raw.items() if k != SECRETS_KEY}
    flat_strings = _flatten(raw)
    flat_typed = _flatten_typed(raw)
    resolved_strings: dict[str, str] = {}
    for key, value in flat_strings.items():
        resolved_strings[key] = _resolve_env_vars(value)
    return resolved_strings, flat_typed


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

    # `secrets:` is a declaration ABOUT the properties, not a property. Leaving it in would create
    # phantom entries (`secrets.0`) and make the list itself interpolatable.
    raw = {k: v for k, v in raw.items() if k != SECRETS_KEY}
    flat = _flatten(raw)

    resolved: dict[str, str] = {}
    for key, value in flat.items():
        resolved[key] = _resolve_env_vars(str(value))

    return resolved


#: A string that is EXACTLY one ``${name}`` or ``${name:-default}`` with no surrounding
#: text. Captured name goes to group 1, default (if any) to group 2.
_WHOLE_REF = re.compile(r"^\$\{([^}:]+)(?::-(.*))?\}$")


def interpolate_value(  # noqa: PLR0911
    value: Any,
    properties: dict[str, str],
    typed_properties: dict[str, Any] | None = None,
) -> Any:
    """Resolve ${property.path} references in a value.

    - Strings with ``${...}`` get property substitution.
    - Dicts and lists are traversed recursively.
    - Non-string values pass through unchanged.
    - When ``typed_properties`` is provided AND the value is a **whole-string
      reference** (the entire value is exactly ``${name}`` or ``${name:-default}``,
      with no surrounding text), the typed property is returned instead of its
      string form. Mixed strings (``"port ${p} open"``) keep today's behaviour
      because concatenation only makes sense on strings. #879.
    """
    if isinstance(value, str):
        if typed_properties is not None:
            match = _WHOLE_REF.match(value)
            if match:
                name = match.group(1)
                if name in typed_properties:
                    typed = typed_properties[name]
                    # A non-string typed leaf (int/float/bool/null/list/dict) is the whole
                    # point of #879 — return it verbatim. A string typed leaf may itself
                    # contain ${ENV} / ${ENV:-default}; fall through to the string-expanded
                    # view so #1072 isn't a regression on #879.
                    if not isinstance(typed, str):
                        return typed
                    return properties.get(name, typed)
                # Fall back to the env-var lookup; the result is a string by nature
                # (os.environ values are strings), so string-coercion of a default is
                # the honest answer here too.
                env_val = os.environ.get(name)
                if env_val is not None:
                    return env_val
                default = match.group(2)
                if default is not None:
                    return default
                # Unresolved — leave literal, matching the mixed-string path below.
        return _substitute_properties(value, properties)
    if isinstance(value, dict):
        return {k: interpolate_value(v, properties, typed_properties) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_value(item, properties, typed_properties) for item in value]
    return value


def interpolate_dict(
    data: dict[str, Any],
    properties: dict[str, str],
    typed_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve all ${property.path} references in a dict tree.

    ``typed_properties`` is forwarded to :func:`interpolate_value` for #879's
    whole-string-reference type preservation.
    """
    return {k: interpolate_value(v, properties, typed_properties) for k, v in data.items()}


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict to dotted key paths (string-coerced leaves)."""
    result: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            result.update(_flatten(value, full_key))
        else:
            result[full_key] = str(value)
    return result


def _flatten_typed(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict to dotted key paths, preserving the original leaf type.

    Used by :func:`load_env_config_typed` so a numeric ``model.vision.num_ctx: 4096``
    stays an ``int`` and the whole-string-reference branch of ``interpolate_value``
    can return it unchanged. See #879.
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            result.update(_flatten_typed(value, full_key))
        else:
            result[full_key] = value
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
