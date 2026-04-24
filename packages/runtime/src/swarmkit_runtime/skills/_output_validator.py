"""Deterministic output validation for skills with declared ``outputs``.

Tiers 0-2 of the output governance model. See
``design/details/structured-output-governance.md``.

Outputs are now standard JSON Schema — validation uses ``jsonschema``
directly. Field-specific errors are extracted from validation failures
for targeted auto-correction re-prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import jsonschema


@dataclass(frozen=True)
class FieldError:
    """A validation error on a specific output field."""

    field: str
    message: str


def validate_skill_output(
    output: dict[str, Any],
    outputs_schema: dict[str, Any],
) -> list[FieldError]:
    """Validate a skill's output against its JSON Schema ``outputs`` block.

    Returns an empty list if valid. Each error is field-specific so the
    auto-correction re-prompt can target exactly what needs fixing.
    """
    errors: list[FieldError] = []
    try:
        jsonschema.validate(instance=output, schema=outputs_schema)
    except jsonschema.ValidationError as exc:
        field = _extract_field_path(exc)
        errors.append(FieldError(field, exc.message))
        for sub in exc.context or []:
            sub_field = _extract_field_path(sub)
            errors.append(FieldError(sub_field, sub.message))
    return errors


def validate_all_skill_output(
    output: dict[str, Any],
    outputs_schema: dict[str, Any],
) -> list[FieldError]:
    """Like ``validate_skill_output`` but collects ALL errors, not just the first."""
    validator_cls = jsonschema.Draft202012Validator
    validator = validator_cls(outputs_schema)
    errors: list[FieldError] = []
    for error in sorted(validator.iter_errors(output), key=lambda e: list(e.path)):
        field = _extract_field_path(error)
        errors.append(FieldError(field, error.message))
    return errors


def validate_business_rules(
    output: dict[str, Any],
    rules: list[dict[str, Any]],
) -> list[FieldError]:
    """Evaluate deterministic business rules (Tier 2)."""
    errors: list[FieldError] = []
    for rule in rules:
        error = _evaluate_rule(output, rule)
        if error is not None:
            errors.append(error)
    return errors


def format_correction_prompt(errors: list[FieldError]) -> str:
    """Format field errors into a targeted re-prompt for the model."""
    lines = ["Your response had validation errors on these fields:"]
    for err in errors:
        lines.append(f"  - {err.field}: {err.message}")
    lines.append("Correct ONLY these fields and return the full response.")
    return "\n".join(lines)


# ---- helpers -------------------------------------------------------------


def _extract_field_path(error: jsonschema.ValidationError) -> str:
    """Extract the field name from a jsonschema error's path."""
    if error.path:
        return ".".join(str(p) for p in error.path)
    if error.schema_path and len(error.schema_path) > 1:
        for segment in reversed(list(error.schema_path)):
            if isinstance(segment, str) and segment not in (
                "type",
                "properties",
                "required",
                "enum",
                "minimum",
                "maximum",
                "minLength",
                "items",
                "additionalProperties",
            ):
                return segment
    return "(root)"


# ---- business rules (Tier 2) -------------------------------------------


def _evaluate_rule(output: dict[str, Any], rule: dict[str, Any]) -> FieldError | None:
    if "if" in rule and "then" in rule:
        return _evaluate_conditional_rule(output, rule)
    if "field" in rule:
        return _evaluate_field_rule(output, rule)
    return None


def _evaluate_conditional_rule(
    output: dict[str, Any],
    rule: dict[str, Any],
) -> FieldError | None:
    condition = rule["if"]
    then_checks = rule["then"]
    message = rule.get("message", "conditional rule failed")

    if not all(output.get(k) == v for k, v in condition.items()):
        return None

    for field_name, constraints in then_checks.items():
        value = output.get(field_name)
        if value is None or not isinstance(value, (int, float)):
            continue
        if "max" in constraints and value > constraints["max"]:
            return FieldError(field_name, message)
        if "min" in constraints and value < constraints["min"]:
            return FieldError(field_name, message)
    return None


def _evaluate_field_rule(
    output: dict[str, Any],
    rule: dict[str, Any],
) -> FieldError | None:
    field_name = rule["field"]
    value = output.get(field_name)
    message = rule.get("message", f"validation failed for {field_name}")

    if value is None:
        return None

    if isinstance(value, str):
        if "min_length" in rule and len(value) < rule["min_length"]:
            return FieldError(field_name, message)
        if "max_length" in rule and len(value) > rule["max_length"]:
            return FieldError(field_name, message)
        if "pattern" in rule and not re.match(rule["pattern"], value):
            return FieldError(field_name, message)

    return None
