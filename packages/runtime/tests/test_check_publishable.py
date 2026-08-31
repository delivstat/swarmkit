"""The release guard counts only files that ship, and compares against the right baseline.

`scripts/check_publishable.py` fails a release when a changed package's version is already on PyPI,
because `publish_if_new` silently skips it. It has now missed that failure twice for the same
reason, and both misses shipped:

* **swarmkit-schema** sat at 1.23.0 across six releases, so a published runtime rejected artifacts
  its own schema had been extended to accept.
* **swarmkit-webui** sat at 0.14.0 while six commits changed the UI — including the one removing the
  bundled pipeline — so the published bundle kept a Pipelines section calling an API that no longer
  existed. A user reported it; nothing in CI could have.

Both had one cause: the baseline was the **last tag**. A version frozen across several releases
stops looking changed once its change falls out of that one-tag window — exactly the case the guard
exists for. The baseline is now the commit that last SET the version.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[3]
_SCRIPT = REPO / "scripts" / "check_publishable.py"
_spec = importlib.util.spec_from_file_location("check_publishable", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestOnlyShippedFilesCount:
    """A test beside the source it tests is inside a package directory and in no artifact.
    Counting it demands a version bump that would publish a byte-identical release, and a PyPI
    version is permanent — the opposite failure to the one the guard exists for."""

    @pytest.mark.parametrize(
        "path",
        [
            "packages/ui/lib/schema-form.test.ts",
            "packages/ui/components/thing.test.tsx",
            "packages/ui/lib/thing.spec.ts",
            "packages/runtime/src/swarmkit_runtime/test_something.py",
        ],
    )
    def test_tests_do_not_count(self, path: str) -> None:
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
    def test_real_source_still_counts(self, path: str) -> None:
        """`contest.ts` and `latest-version.ts` are here because a naive substring check for
        'test' or 'spec' would drop both."""
        assert _mod._ships(path) is True


class TestBaselineIsThePackagesOwnReleasePoint:
    def test_the_baseline_is_the_commit_that_set_the_version(self) -> None:
        """Not the last tag. This is the whole fix — the webui miss was invisible from one tag."""
        for _name, pyproject, _sources in _mod.PACKAGES:
            commit = _mod._version_set_at(pyproject)
            assert commit, f"no version commit found for {pyproject}"
            touched = subprocess.run(
                ["git", "show", "--name-only", "--format=", commit],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert pyproject in touched, f"{commit[:9]} should have changed {pyproject}"

    def test_the_check_reads_the_version_commit_not_the_tag(self) -> None:
        """The fix, asserted on source. `_last_tag` survives only as a fallback for a package
        whose pyproject has no matching version line — reintroducing it as the baseline would
        restore the one-tag window both misses came through, and no behavioural test would fail
        because the window only matters across releases nobody replays."""
        body = _SCRIPT.read_text()
        main = body[body.index("def main(") :]
        assert "_version_set_at(pyproject)" in main, "the baseline must come from the version line"
        assert "_changed_since(baseline" in main, "the diff must start at that baseline"
        assert "_changed_since(tag" not in main, "comparing against the tag is the bug"

    def test_a_pyproject_without_a_version_line_falls_back_rather_than_crashing(
        self, tmp_path: Path
    ) -> None:
        """A packaging error in one manifest must not take the whole release check down."""
        assert _mod._version_set_at("packages/runtime/README.md") is None


class TestPyprojectIsWatchedContent:
    """Dependencies, extras and entry points ship in the wheel metadata. Tightening a version
    floor is a change users need, and the old path list could not see it — which is how the
    `swarmkit-webui>=0.1.0` floor could have been raised and never reached anyone."""

    def test_every_package_watches_its_own_pyproject(self) -> None:
        for _name, pyproject, sources in _mod.PACKAGES:
            assert pyproject in sources, f"{pyproject} is shipped metadata and must be watched"

    def test_the_ui_extra_is_floored(self) -> None:
        """`>=0.1.0` cannot express "this runtime needs a UI that knows the pipeline is gone"."""
        text = (REPO / "packages" / "runtime" / "pyproject.toml").read_text()
        assert "swarmkit-webui>=0.1.0" not in text, "an unbounded UI floor lets a stale bundle pair"
        assert "swarmkit-webui>=0." in text
