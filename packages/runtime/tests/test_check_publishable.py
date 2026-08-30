"""The release guard counts only files that actually ship.

`scripts/check_publishable.py` fails a release when a changed package's version is already on PyPI,
because `publish_if_new` would silently skip it — that shipped four unreachable features once
(`docs/notes/release-version-discipline.md`).

Counting a *test* as a change inverts it: the guard demands a version bump that would publish a
byte-identical release, and a PyPI version is permanent. So both directions are tested here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[3] / "scripts" / "check_publishable.py"
_spec = importlib.util.spec_from_file_location("check_publishable", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


@pytest.mark.parametrize(
    "path",
    [
        "packages/ui/lib/schema-form.test.ts",
        "packages/ui/components/thing.test.tsx",
        "packages/ui/lib/thing.spec.ts",
        "packages/runtime/src/swarmkit_runtime/test_helper_test.py",
        "packages/runtime/src/swarmkit_runtime/test_something.py",
    ],
)
def test_tests_do_not_count_as_a_change(path: str) -> None:
    """A Next.js production build and a wheel both exclude these, so the artifact is identical."""
    assert _mod._ships(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "packages/ui/lib/schema-form.ts",
        "packages/ui/app/page.tsx",
        "packages/runtime/src/swarmkit_runtime/commands/_runner.py",
        "packages/schema/schemas/workspace.schema.json",
        "packages/ui/lib/latest-version.ts",
        "packages/ui/lib/contest.ts",
    ],
)
def test_real_source_still_counts(path: str) -> None:
    """The failure the guard exists for. `contest.ts` and `latest-version.ts` are here because a
    naive substring check for 'test' or 'spec' would drop them."""
    assert _mod._ships(path) is True
