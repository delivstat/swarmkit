"""Deterministic-check tests (pure, no runtime)."""

from __future__ import annotations

from swarmkit_runtime.eval import ExpectSpec, deterministic_checks


def _passed(output: str, expect: ExpectSpec) -> bool:
    checks = deterministic_checks(output, expect)
    return bool(checks) and all(c.passed for c in checks)


def test_contains_case_insensitive() -> None:
    assert _passed("Hello Engineers", ExpectSpec(contains=["engineer", "hello"]))
    assert not _passed("Hello", ExpectSpec(contains=["engineer"]))


def test_not_contains() -> None:
    assert _passed("a polite greeting", ExpectSpec(not_contains=["error", "sorry"]))
    assert not _passed("an ERROR happened", ExpectSpec(not_contains=["error"]))


def test_regex() -> None:
    assert _passed("order #4821", ExpectSpec(regex=r"#\d+"))
    assert not _passed("no number", ExpectSpec(regex=r"#\d+"))


def test_equals_trims() -> None:
    assert _passed("  yes  ", ExpectSpec(equals="yes"))
    assert not _passed("no", ExpectSpec(equals="yes"))


def test_not_empty() -> None:
    assert _passed("x", ExpectSpec(not_empty=True))
    assert not _passed("   ", ExpectSpec(not_empty=True))


def test_no_expectations_yields_no_checks() -> None:
    assert deterministic_checks("anything", ExpectSpec()) == []
