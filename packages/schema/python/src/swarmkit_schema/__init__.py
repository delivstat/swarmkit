"""SwarmKit schema validators (Python).

The JSON Schema files under `packages/schema/schemas/` are the source of truth —
this package only validates against them. See `packages/schema/CLAUDE.md` for
the dual-surface rule.

Resolution order:
  1. `_schemas/` inside the installed package (wheel builds via hatch
     `force-include`).
  2. `packages/schema/schemas/` relative to this file (editable / dev mode).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import jsonschema

__version__ = "0.0.1"

SchemaName = Literal["topology", "skill", "archetype", "workspace", "trigger"]

_HERE = Path(__file__).resolve().parent


def _schema_root() -> Path:
    installed = _HERE / "_schemas"
    if installed.is_dir():
        return installed
    # Editable dev mode: packages/schema/python/src/swarmkit_schema → ../../../schemas
    dev = _HERE.parent.parent.parent / "schemas"
    if dev.is_dir():
        return dev
    raise RuntimeError(f"Canonical JSON Schemas not found. Looked in:\n  {installed}\n  {dev}")


def _load(name: SchemaName) -> dict[str, Any]:
    path = _schema_root() / f"{name}.schema.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def get_schema(name: SchemaName) -> dict[str, Any]:
    """Return the canonical JSON Schema for the given artifact type."""
    return _load(name)


def validate(name: SchemaName, instance: Any) -> None:
    """Validate `instance` against the named schema. Raises `ValidationError` on failure."""
    schema = _load(name)
    jsonschema.validate(instance=instance, schema=schema)


__all__ = ["SchemaName", "__version__", "get_schema", "validate"]
