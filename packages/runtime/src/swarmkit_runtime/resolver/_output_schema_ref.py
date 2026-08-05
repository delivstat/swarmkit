"""Resolving an ``output_schema`` that names a file, and validating one that does not.

``output_schema`` accepts an inline object, a path to a JSON Schema file, or ``null`` to opt out.
Both forms normalise to the parsed object before anything downstream sees them, so a consumer cannot
tell which was written — the file is an authoring convenience, not a runtime concept.

Everything here fails at **load** time. A malformed schema used to surface mid-run as a conformance
failure, which reads like the agent's fault rather than the topology's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Suffixes a schema file may use. YAML is accepted because every other artifact here is YAML and a
#: schema author reasonably reaches for it.
_YAML_SUFFIXES = {".yaml", ".yml"}


class OutputSchemaError(ValueError):
    """A declared output_schema could not be resolved or is not a valid JSON Schema."""


def resolve_output_schema(
    value: Any,
    *,
    declared_in: Path,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Return the parsed schema for an inline object or a file path.

    ``declared_in`` is the artifact that carries the declaration; a relative path resolves against
    its directory, because a schema usually lives beside the topology that uses it.
    """
    if isinstance(value, dict):
        _assert_valid_schema(value, source=str(declared_in))
        return dict(value)
    if not isinstance(value, str):  # pragma: no cover - schema-guarded
        raise OutputSchemaError(
            f"output_schema must be an object, a path or null; got {type(value).__name__}"
        )

    path = _resolve_path(value, declared_in=declared_in, workspace_root=workspace_root)
    try:
        text = path.read_text()
    except OSError as exc:
        raise OutputSchemaError(
            f"output_schema file {value!r} declared in {declared_in} could not be read: {exc}"
        ) from exc

    try:
        if path.suffix.lower() in _YAML_SUFFIXES:
            import yaml  # noqa: PLC0415

            parsed = yaml.safe_load(text)
        else:
            parsed = json.loads(text)
    except Exception as exc:
        raise OutputSchemaError(f"output_schema file {path} does not parse: {exc}") from exc

    if not isinstance(parsed, dict):
        raise OutputSchemaError(
            f"output_schema file {path} must contain a JSON Schema object, "
            f"got {type(parsed).__name__}"
        )
    _assert_valid_schema(parsed, source=str(path))
    return parsed


def _resolve_path(value: str, *, declared_in: Path, workspace_root: Path | None) -> Path:
    """Resolve a declared path, refusing anything that leaves the workspace.

    A remote URL is refused outright: a schema fetched at resolve time would make a workspace's
    meaning depend on the network, and on whatever the other end serves that day.
    """
    if "://" in value:
        raise OutputSchemaError(
            f"output_schema {value!r} looks like a URL. Only files inside the workspace are "
            f"accepted, so a workspace's meaning does not depend on the network."
        )
    candidate = Path(value)
    base = declared_in.parent if declared_in.suffix else declared_in
    target = (candidate if candidate.is_absolute() else base / candidate).resolve()

    if workspace_root is not None:
        root = Path(workspace_root).resolve()
        if target != root and not target.is_relative_to(root):
            raise OutputSchemaError(
                f"output_schema {value!r} declared in {declared_in} resolves to {target}, which is "
                f"outside the workspace {root}."
            )
    if not target.is_file():
        raise OutputSchemaError(
            f"output_schema file {value!r} declared in {declared_in} not found (looked in {target})"
        )
    return target


def _assert_valid_schema(schema: dict[str, Any], *, source: str) -> None:
    """Check the document really is a JSON Schema.

    Inline schemas get this too, which they did not before. A typo in a `required` list used to
    surface only when an agent's output was measured against it — mid-run, blaming the agent.
    """
    import jsonschema  # noqa: PLC0415

    validator = jsonschema.validators.validator_for(schema)
    try:
        validator.check_schema(schema)
    except jsonschema.exceptions.SchemaError as exc:
        raise OutputSchemaError(
            f"output_schema in {source} is not a valid JSON Schema: {exc.message}"
        ) from exc


__all__ = ["OutputSchemaError", "resolve_output_schema"]
