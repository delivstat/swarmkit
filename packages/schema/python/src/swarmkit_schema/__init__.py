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
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import jsonschema
from referencing import Registry, Resource

__version__ = "0.0.1"

SchemaName = Literal[
    "topology",
    "skill",
    "archetype",
    "workspace",
    "trigger",
    "executor-adapter",
    "role-registry",
    "approval-policy",
    "funnel",
]

#: Fleet-enrollment wire schemas (design details/control-plane/19-fleet-enrollment-protocol.md).
#: Distinct from the artifact schemas above — these are API request/response contracts a third-party
#: client validates against, not user-authored artifacts, so they are not run through codegen.
ProtocolSchemaName = Literal[
    "credential",
    "fleet-identity",
    "instance-state",
    "register-request",
    "register-response",
    "join-request",
    "join-response",
]

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


# --- fleet-enrollment protocol schemas --------------------------------------
# The wire contract lives under schemas/protocol/. The message schemas cross-reference each other
# by $id (register/join responses embed the credential + instance-state schemas), so validation goes
# through a `referencing.Registry` that has every protocol schema loaded — this makes each file
# independently publishable while the $refs still resolve.


def _protocol_root() -> Path:
    return _schema_root() / "protocol"


def _load_protocol(name: ProtocolSchemaName) -> dict[str, Any]:
    path = _protocol_root() / f"{name}.schema.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@lru_cache(maxsize=1)
def _protocol_registry() -> Registry:
    """A registry of every protocol schema, keyed by `$id`, so cross-file `$ref`s resolve."""
    resources = [
        (schema["$id"], Resource.from_contents(schema))
        for schema in (
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(_protocol_root().glob("*.schema.json"))
        )
    ]
    return Registry().with_resources(resources)


def get_protocol_schema(name: ProtocolSchemaName) -> dict[str, Any]:
    """Return the canonical JSON Schema for a fleet-enrollment message (design 19)."""
    return _load_protocol(name)


def validate_protocol(name: ProtocolSchemaName, instance: Any) -> None:
    """Validate `instance` against a fleet-enrollment message schema (register/join/InstanceState/
    credential). Raises `ValidationError` on failure. Cross-file `$ref`s resolve via the
    protocol registry."""
    schema = _load_protocol(name)
    validator = jsonschema.Draft202012Validator(schema, registry=_protocol_registry())
    validator.validate(instance)


__all__ = [
    "ProtocolSchemaName",
    "SchemaName",
    "__version__",
    "get_protocol_schema",
    "get_schema",
    "validate",
    "validate_protocol",
]
