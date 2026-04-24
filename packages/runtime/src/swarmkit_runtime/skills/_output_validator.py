"""Deterministic output validation for skills with declared ``outputs``.

Tiers 0-2 of the output governance model. See
``design/details/structured-output-governance.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldError:
    """A validation error on a specific output field."""

    field: str
    message: str


def validate_skill_output(
    output: dict[str, Any],
    outputs_spec: dict[str, Any],
) -> list[FieldError]:
    """Validate a skill's output against its declared ``outputs`` schema.

    Returns an empty list if valid. Each error is field-specific so the
    auto-correction re-prompt can target exactly what needs fixing.
    """
    errors: list[FieldError] = []
    for field_name, field_spec in outputs_spec.items():
        if field_name not in output:
            errors.append(FieldError(field_name, "missing required field"))
            continue
        value = output[field_name]
        field_type = field_spec.get("type", "string")
        errors.extend(_validate_field(field_name, value, field_type, field_spec))
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


# ---- field-level validation (Tier 1) ------------------------------------


def _validate_field(
    field_name: str,
    value: Any,
    field_type: str,
    spec: dict[str, Any],
) -> list[FieldError]:
    validators = {
        "enum": _validate_enum,
        "number": _validate_number,
        "string": _validate_string,
        "array": _validate_array,
        "object": _validate_object,
    }
    validator = validators.get(field_type)
    if validator is not None:
        return validator(field_name, value, spec)
    return []


def _validate_enum(name: str, value: Any, spec: dict[str, Any]) -> list[FieldError]:
    allowed = spec.get("values", [])
    if value not in allowed:
        return [FieldError(name, f"must be one of {allowed}, got '{value}'")]
    return []


def _validate_number(name: str, value: Any, spec: dict[str, Any]) -> list[FieldError]:
    if not isinstance(value, (int, float)):
        return [FieldError(name, f"must be a number, got {type(value).__name__}")]
    range_ = spec.get("range")
    if range_ and len(range_) == 2 and not (range_[0] <= value <= range_[1]):
        return [FieldError(name, f"must be between {range_[0]} and {range_[1]}, got {value}")]
    return []


def _validate_string(name: str, value: Any, spec: dict[str, Any]) -> list[FieldError]:
    if not isinstance(value, str):
        return [FieldError(name, f"must be a string, got {type(value).__name__}")]
    min_len = spec.get("min_length")
    if min_len and len(value) < min_len:
        return [FieldError(name, f"must be at least {min_len} characters, got {len(value)}")]
    return []


def _validate_array(name: str, value: Any, _spec: dict[str, Any]) -> list[FieldError]:
    if not isinstance(value, list):
        return [FieldError(name, f"must be an array, got {type(value).__name__}")]
    return []


def _validate_object(name: str, value: Any, _spec: dict[str, Any]) -> list[FieldError]:
    if not isinstance(value, dict):
        return [FieldError(name, f"must be an object, got {type(value).__name__}")]
    return []


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
