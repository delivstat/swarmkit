"""Topology ``input_schema`` — validate the caller's input before any agent runs (input-schema.md).

Validate-and-reject at the single choke point (``WorkspaceRuntime.run``), so every entry inherits
it: a malformed request never becomes a billable run. Symmetric to ``output_schema`` but there is
no re-prompt — a caller cannot be corrected mid-run, so the run simply does not start.
"""

from __future__ import annotations

import json
from typing import Any

from swarmkit_runtime.skills._output_validator import validate_against_schema


class InputValidationError(Exception):
    """The caller's input did not satisfy the topology's ``input_schema``. Mapped to 422 over HTTP,
    a non-zero exit on the CLI, and a JSON-RPC error over A2A. Carries the field-specific messages.
    """

    def __init__(self, message: str, *, fields: list[str] | None = None) -> None:
        super().__init__(message)
        self.fields = fields or []


def _requires_json(schema: dict[str, Any]) -> bool:
    """An object/array schema (or one keyed on object structure) requires JSON input; a
    ``{"type": "string"}`` schema validates raw text."""
    return schema.get("type") in ("object", "array") or bool(
        {"properties", "required", "additionalProperties"} & schema.keys()
    )


def check_entry_input(user_input: str, input_schema: dict[str, Any] | None) -> None:
    """Raise :class:`InputValidationError` if *user_input* does not satisfy *input_schema*.

    No-op when the topology declares no ``input_schema``. The input arrives as a string; an
    object/array schema requires it to parse as JSON (a non-JSON body is rejected outright), while
    a string/scalar schema validates the value — parsed as JSON when it parses, otherwise the raw
    string, so ``{"type": "string"}`` accepts plain natural-language text.
    """
    if not input_schema:
        return
    if _requires_json(input_schema):
        try:
            instance: Any = json.loads(user_input)
        except (json.JSONDecodeError, TypeError):
            raise InputValidationError(
                "input must be a JSON object matching input_schema"
            ) from None
    else:
        try:
            instance = json.loads(user_input)
        except (json.JSONDecodeError, TypeError):
            instance = user_input
    errors = validate_against_schema(instance, input_schema)
    if errors:
        detail = "; ".join(f"{e.field}: {e.message}" if e.field else e.message for e in errors)
        raise InputValidationError(
            f"input does not satisfy input_schema: {detail}",
            fields=[e.field for e in errors if e.field],
        )
