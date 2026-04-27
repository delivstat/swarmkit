"""Tests for deterministic output validation (M4, task #43).

See ``design/details/structured-output-governance.md``.
"""

from __future__ import annotations

import pytest
from swael_runtime.skills._output_validator import (
    FieldError,
    format_correction_prompt,
    validate_all_skill_output,
    validate_business_rules,
    validate_skill_output,
)

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "confidence", "reasoning"],
}


# ---- Tier 1: JSON Schema validation -------------------------------------


def test_valid_output_passes() -> None:
    output = {"verdict": "pass", "confidence": 0.85, "reasoning": "Looks good."}
    errors = validate_skill_output(output, DECISION_SCHEMA)
    assert errors == []


def test_missing_field_detected() -> None:
    output = {"verdict": "pass", "confidence": 0.85}
    errors = validate_skill_output(output, DECISION_SCHEMA)
    assert len(errors) >= 1
    assert any("reasoning" in e.message for e in errors)


def test_enum_wrong_value_detected() -> None:
    output = {"verdict": "maybe", "confidence": 0.5, "reasoning": "Unsure."}
    errors = validate_skill_output(output, DECISION_SCHEMA)
    assert len(errors) >= 1
    assert any("maybe" in e.message or "enum" in e.message.lower() for e in errors)


def test_number_out_of_range_detected() -> None:
    output = {"verdict": "pass", "confidence": 1.5, "reasoning": "Great."}
    errors = validate_skill_output(output, DECISION_SCHEMA)
    assert len(errors) >= 1
    assert any(e.field == "confidence" for e in errors)


def test_wrong_type_detected() -> None:
    output = {"verdict": "pass", "confidence": "high", "reasoning": "Good."}
    errors = validate_skill_output(output, DECISION_SCHEMA)
    assert len(errors) >= 1
    assert any(e.field == "confidence" for e in errors)


def test_string_type_wrong_detected() -> None:
    output = {"verdict": "pass", "confidence": 0.5, "reasoning": 42}
    errors = validate_skill_output(output, DECISION_SCHEMA)
    assert len(errors) >= 1
    assert any(e.field == "reasoning" for e in errors)


def test_validate_all_collects_multiple_errors() -> None:
    output = {"verdict": "maybe", "confidence": 1.5, "reasoning": 42}
    errors = validate_all_skill_output(output, DECISION_SCHEMA)
    assert len(errors) >= 3
    fields = {e.field for e in errors}
    assert "verdict" in fields
    assert "confidence" in fields
    assert "reasoning" in fields


def test_array_type_validated() -> None:
    schema = {
        "type": "object",
        "properties": {"items": {"type": "array"}},
        "required": ["items"],
    }
    errors = validate_skill_output({"items": "not-an-array"}, schema)
    assert len(errors) >= 1


def test_min_length_validated() -> None:
    schema = {
        "type": "object",
        "properties": {"reasoning": {"type": "string", "minLength": 20}},
        "required": ["reasoning"],
    }
    errors = validate_skill_output({"reasoning": "too short"}, schema)
    assert len(errors) >= 1


# ---- Tier 2: business rules ---------------------------------------------


def test_conditional_rule_passes_when_condition_not_met() -> None:
    rules = [
        {
            "if": {"verdict": "fail"},
            "then": {"confidence": {"max": 0.5}},
            "message": "Failed verdict should have low confidence",
        }
    ]
    errors = validate_business_rules({"verdict": "pass", "confidence": 0.9}, rules)
    assert errors == []


def test_conditional_rule_passes_when_constraint_met() -> None:
    rules = [
        {
            "if": {"verdict": "fail"},
            "then": {"confidence": {"max": 0.5}},
            "message": "Failed verdict should have low confidence",
        }
    ]
    errors = validate_business_rules({"verdict": "fail", "confidence": 0.3}, rules)
    assert errors == []


def test_conditional_rule_fails_when_constraint_violated() -> None:
    rules = [
        {
            "if": {"verdict": "fail"},
            "then": {"confidence": {"max": 0.5}},
            "message": "Failed verdict should have confidence <= 0.5",
        }
    ]
    errors = validate_business_rules({"verdict": "fail", "confidence": 0.8}, rules)
    assert len(errors) == 1
    assert errors[0].field == "confidence"
    assert "0.5" in errors[0].message


def test_field_rule_min_length() -> None:
    rules = [
        {"field": "reasoning", "min_length": 20, "message": "Reasoning too short"},
    ]
    errors = validate_business_rules({"reasoning": "ok"}, rules)
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
        FieldError("reasoning", "must be at least 20 characters"),
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
