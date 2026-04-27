"""Tests for multi-persona panel aggregation (M4).

See ``design/details/decision-skills.md`` §Multi-persona panels.
"""

from __future__ import annotations

from swael_runtime.langgraph_compiler._panel import (
    _aggregate_first_fail,
    _aggregate_majority,
)


def test_majority_pass() -> None:
    votes = [
        {"judge": "j1", "verdict": "pass", "confidence": 0.9, "reasoning": "good"},
        {"judge": "j2", "verdict": "pass", "confidence": 0.8, "reasoning": "fine"},
        {"judge": "j3", "verdict": "fail", "confidence": 0.7, "reasoning": "bad"},
    ]
    result = _aggregate_majority(votes)
    assert result["verdict"] == "pass"
    assert result["confidence"] == 0.85
    assert len(result["panel_votes"]) == 3


def test_majority_fail() -> None:
    votes = [
        {"judge": "j1", "verdict": "fail", "confidence": 0.9, "reasoning": "bad"},
        {"judge": "j2", "verdict": "fail", "confidence": 0.7, "reasoning": "wrong"},
        {"judge": "j3", "verdict": "pass", "confidence": 0.8, "reasoning": "ok"},
    ]
    result = _aggregate_majority(votes)
    assert result["verdict"] == "fail"
    assert len(result["panel_votes"]) == 3


def test_majority_tie_defaults_to_fail() -> None:
    votes = [
        {"judge": "j1", "verdict": "pass", "confidence": 0.9, "reasoning": "ok"},
        {"judge": "j2", "verdict": "fail", "confidence": 0.8, "reasoning": "bad"},
    ]
    result = _aggregate_majority(votes)
    assert result["verdict"] == "fail"


def test_first_fail_stops_chain() -> None:
    votes = [
        {"judge": "j1", "verdict": "pass", "confidence": 0.9, "reasoning": "ok"},
        {"judge": "j2", "verdict": "fail", "confidence": 0.3, "reasoning": "security issue"},
    ]
    result = _aggregate_first_fail(votes)
    assert result["verdict"] == "fail"
    assert "j2" in result["reasoning"]
    assert result["confidence"] == 0.3


def test_majority_confidence_averages_agreeing_judges() -> None:
    votes = [
        {"judge": "j1", "verdict": "pass", "confidence": 0.9},
        {"judge": "j2", "verdict": "pass", "confidence": 0.7},
        {"judge": "j3", "verdict": "fail", "confidence": 0.5},
    ]
    result = _aggregate_majority(votes)
    assert result["confidence"] == 0.8


def test_panel_votes_preserved() -> None:
    votes = [
        {"judge": "j1", "verdict": "pass", "confidence": 0.9, "reasoning": "a"},
        {"judge": "j2", "verdict": "fail", "confidence": 0.6, "reasoning": "b"},
    ]
    result = _aggregate_majority(votes)
    assert result["panel_votes"] == votes


def test_error_vote_counted_as_non_pass() -> None:
    votes = [
        {"judge": "j1", "verdict": "pass", "confidence": 0.9, "reasoning": "ok"},
        {"judge": "j2", "verdict": "error", "confidence": 0.0, "reasoning": "failed"},
        {"judge": "j3", "verdict": "fail", "confidence": 0.5, "reasoning": "bad"},
    ]
    result = _aggregate_majority(votes)
    assert result["verdict"] == "fail"
