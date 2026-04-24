"""Tests for deterministic output validation (M4, task #43).

See ``design/details/structured-output-governance.md``.
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.skills._output_validator import (
    FieldError,
    format_correction_prompt,
    validate_business_rules,
    validate_skill_output,
)

DECISION_SPEC = {
    "verdict": {"type": "enum", "values": ["pass", "fail"]},
    "confidence": {"type": "number", "range": [0, 1]},
    "reasoning": {"type": "string"},
}


# ---- Tier 1: schema validation ------------------------------------------


def test_valid_output_passes() -> None:
    output = {"verdict": "pass", "confidence": 0.85, "reasoning": "Looks good."}
    errors = validate_skill_output(output, DECISION_SPEC)
    assert errors == []


def test_missing_field_detected() -> None:
    output = {"verdict": "pass", "confidence": 0.85}
    errors = validate_skill_output(output, DECISION_SPEC)
    assert len(errors) == 1
    assert errors[0].field == "reasoning"
    assert "missing" in errors[0].message


def test_enum_wrong_value_detected() -> None:
    output = {"verdict": "maybe", "confidence": 0.5, "reasoning": "Unsure."}
    errors = validate_skill_output(output, DECISION_SPEC)
    assert len(errors) == 1
    assert errors[0].field == "verdict"
    assert "pass" in errors[0].message
    assert "maybe" in errors[0].message


def test_number_out_of_range_detected() -> None:
    output = {"verdict": "pass", "confidence": 1.5, "reasoning": "Great."}
    errors = validate_skill_output(output, DECISION_SPEC)
    assert len(errors) == 1
    assert errors[0].field == "confidence"
    assert "1.5" in errors[0].message
    assert "0" in errors[0].message and "1" in errors[0].message


def test_wrong_type_detected() -> None:
    output = {"verdict": "pass", "confidence": "high", "reasoning": "Good."}
    errors = validate_skill_output(output, DECISION_SPEC)
    assert len(errors) == 1
    assert errors[0].field == "confidence"
    assert "number" in errors[0].message


def test_string_type_wrong_detected() -> None:
    output = {"verdict": "pass", "confidence": 0.5, "reasoning": 42}
    errors = validate_skill_output(output, DECISION_SPEC)
    assert len(errors) == 1
    assert errors[0].field == "reasoning"
    assert "string" in errors[0].message


def test_multiple_errors_detected() -> None:
    output = {"verdict": "maybe", "confidence": 1.5, "reasoning": 42}
    errors = validate_skill_output(output, DECISION_SPEC)
    assert len(errors) == 3
    fields = {e.field for e in errors}
    assert fields == {"verdict", "confidence", "reasoning"}


def test_array_type_validated() -> None:
    spec = {"items": {"type": "array"}}
    errors = validate_skill_output({"items": "not-an-array"}, spec)
    assert len(errors) == 1
    assert errors[0].field == "items"


def test_object_type_validated() -> None:
    spec = {"data": {"type": "object"}}
    errors = validate_skill_output({"data": "not-an-object"}, spec)
    assert len(errors) == 1
    assert errors[0].field == "data"


def test_string_min_length() -> None:
    spec = {"reasoning": {"type": "string", "min_length": 20}}
    errors = validate_skill_output({"reasoning": "too short"}, spec)
    assert len(errors) == 1
    assert "20 characters" in errors[0].message


# ---- Tier 2: business rules ---------------------------------------------


def test_conditional_rule_passes_when_condition_not_met() -> None:
    rules = [
        {
            "if": {"verdict": "fail"},
            "then": {"confidence": {"max": 0.5}},
            "message": "Failed verdict should have low confidence",
        }
    ]
    output = {"verdict": "pass", "confidence": 0.9}
    errors = validate_business_rules(output, rules)
    assert errors == []


def test_conditional_rule_passes_when_constraint_met() -> None:
    rules = [
        {
            "if": {"verdict": "fail"},
            "then": {"confidence": {"max": 0.5}},
            "message": "Failed verdict should have low confidence",
        }
    ]
    output = {"verdict": "fail", "confidence": 0.3}
    errors = validate_business_rules(output, rules)
    assert errors == []


def test_conditional_rule_fails_when_constraint_violated() -> None:
    rules = [
        {
            "if": {"verdict": "fail"},
            "then": {"confidence": {"max": 0.5}},
            "message": "Failed verdict should have confidence <= 0.5",
        }
    ]
    output = {"verdict": "fail", "confidence": 0.8}
    errors = validate_business_rules(output, rules)
    assert len(errors) == 1
    assert errors[0].field == "confidence"
    assert "0.5" in errors[0].message


def test_field_rule_min_length() -> None:
    rules = [
        {"field": "reasoning", "min_length": 20, "message": "Reasoning too short"},
    ]
    output = {"reasoning": "ok"}
    errors = validate_business_rules(output, rules)
    assert len(errors) == 1
    assert errors[0].field == "reasoning"


def test_field_rule_pattern() -> None:
    rules = [
        {
            "field": "date",
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
            "message": "Date must be ISO 8601",
        },
    ]
    errors = validate_business_rules({"date": "not-a-date"}, rules)
    assert len(errors) == 1

    errors = validate_business_rules({"date": "2026-04-24"}, rules)
    assert errors == []


# ---- correction prompt ---------------------------------------------------


def test_correction_prompt_lists_all_errors() -> None:
    errors = [
        FieldError("confidence", "must be between 0 and 1, got 1.5"),
        FieldError("reasoning", "must be at least 20 characters, got 2"),
    ]
    prompt = format_correction_prompt(errors)
    assert "confidence" in prompt
    assert "reasoning" in prompt
    assert "1.5" in prompt
    assert "Correct ONLY" in prompt


def test_field_error_is_frozen() -> None:
    err = FieldError("f", "m")
    with pytest.raises(AttributeError):
        err.field = "other"  # type: ignore[misc]
