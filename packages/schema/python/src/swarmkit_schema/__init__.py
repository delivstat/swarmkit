"""SwarmKit schema validators (Python).

The JSON Schema files under `swarmkit_schema._schemas` are the source of truth —
this package only validates against them. See `packages/schema/CLAUDE.md` for
the dual-surface rule.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, Literal

import jsonschema

__version__ = "0.0.1"

SchemaName = Literal["topology", "skill", "archetype", "workspace", "trigger"]


def _load(name: SchemaName) -> dict[str, Any]:
    path = resources.files("swarmkit_schema._schemas") / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def get_schema(name: SchemaName) -> dict[str, Any]:
    """Return the canonical JSON Schema for the given artifact type."""
    return _load(name)


def validate(name: SchemaName, instance: Any) -> None:
    """Validate `instance` against the named schema. Raises `ValidationError` on failure."""
    schema = _load(name)
    jsonschema.validate(instance=instance, schema=schema)


__all__ = ["__version__", "SchemaName", "get_schema", "validate"]
