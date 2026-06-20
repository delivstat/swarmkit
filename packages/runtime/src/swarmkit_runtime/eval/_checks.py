"""Deterministic (no-LLM) output checks for an eval case.

Pure functions over the topology output string + the ExpectSpec. Fully unit-testable
without a model provider. The LLM rubric judge lives in the runner (it needs the
runtime); these are the free tier.
"""

from __future__ import annotations

import re

from swarmkit_runtime.eval._models import CheckResult, ExpectSpec


def deterministic_checks(output: str, expect: ExpectSpec) -> list[CheckResult]:
    """Run every set deterministic expectation; return one CheckResult each."""
    results: list[CheckResult] = []
    low = output.lower()

    for sub in expect.contains:
        ok = sub.lower() in low
        results.append(CheckResult("contains", ok, f"{sub!r}{'' if ok else ' missing'}"))

    for sub in expect.not_contains:
        ok = sub.lower() not in low
        results.append(CheckResult("not_contains", ok, f"{sub!r}{'' if ok else ' present'}"))

    if expect.regex is not None:
        ok = re.search(expect.regex, output) is not None
        results.append(CheckResult("regex", ok, expect.regex))

    if expect.equals is not None:
        ok = output.strip() == expect.equals.strip()
        results.append(CheckResult("equals", ok, "" if ok else "output != expected"))

    if expect.not_empty:
        ok = bool(output.strip())
        results.append(CheckResult("not_empty", ok, "" if ok else "output is empty"))

    return results
