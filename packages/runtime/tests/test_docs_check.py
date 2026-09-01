"""The docs checker itself: it must be strict where it matters and quiet where it doesn't.

`scripts/check_docs.py` is only useful if its failures are real. Its first run produced 99 findings
of which most were noise — backticked filenames read as links, "just want to" read as a target,
`services/pipelines/events` read as a removed API. A checker that cries wolf gets muted, which is
indistinguishable from not having one.

So the tuning is under test: the cases that must fire, and the cases that must not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).parents[3]
_spec = importlib.util.spec_from_file_location("check_docs", REPO / "scripts" / "check_docs.py")
assert _spec and _spec.loader
_m = importlib.util.module_from_spec(_spec)
# Registered before exec: `@dataclass` resolves annotations via `sys.modules[cls.__module__]`,
# which is None for a module loaded by path alone.
sys.modules["check_docs"] = _m
_spec.loader.exec_module(_m)


class TestItMustFire:
    def test_a_broken_link_is_found(self, tmp_path: Path) -> None:
        doc = tmp_path / "d.md"
        doc.write_text("See [the note](docs/notes/does-not-exist.md).")
        assert _m.check_paths(doc, doc.read_text())

    def test_a_removed_feature_stated_as_current_is_found(self, tmp_path: Path) -> None:
        doc = tmp_path / "d.md"
        doc.write_text("Run `swarmkit orchestrator` to sequence your stages.")
        assert _m.check_removed(doc, doc.read_text())

    def test_a_missing_just_target_is_found(self, tmp_path: Path) -> None:
        doc = tmp_path / "d.md"
        doc.write_text("Run `just demo-a-target-that-does-not-exist` to see it.")
        assert _m.check_just_targets(doc, doc.read_text())

    def test_a_doc_claiming_the_project_has_not_started_is_found(self, tmp_path: Path) -> None:
        """The claim that made this script necessary."""
        doc = tmp_path / "d.md"
        doc.write_text("**Status:** this repo is currently scaffolding only.")
        assert _m.check_contradicted(doc, doc.read_text())


class TestItMustNotFire:
    """Every case here was a false positive in an earlier round, and each one cost a real fix."""

    def test_a_backticked_filename_in_prose_is_not_a_link(self, tmp_path: Path) -> None:
        doc = tmp_path / "d.md"
        doc.write_text("Harnesses are declared in a `adapter.yaml`, not in Python.")
        assert not _m.check_paths(doc, doc.read_text())

    def test_english_is_not_a_just_target(self, tmp_path: Path) -> None:
        doc = tmp_path / "d.md"
        doc.write_text("If you just want to look around, open the portal.")
        assert not _m.check_just_targets(doc, doc.read_text())

    def test_a_link_inside_a_code_fence_is_an_illustration(self, tmp_path: Path) -> None:
        """`getting-an-image-to-a-model.md` shows a deliberately broken path as its subject."""
        doc = tmp_path / "d.md"
        doc.write_text(
            "Extractors write this:\n\n```markdown\n![](ticket.media/screen1.png)\n```\n"
        )
        assert not _m.check_paths(doc, doc.read_text())

    def test_a_removed_feature_named_as_removed_is_fine(self, tmp_path: Path) -> None:
        doc = tmp_path / "d.md"
        doc.write_text("`swarmkit orchestrator` was removed in 1.189.0.")
        assert not _m.check_removed(doc, doc.read_text())

    def test_the_verb_may_be_on_the_previous_line(self, tmp_path: Path) -> None:
        """Prose wraps. Without a window, a correction gets flagged as the defect it corrects."""
        doc = tmp_path / "d.md"
        doc.write_text(
            "This was removed in 1.189.0, along with\n`kind: StageGraph` and the rest.\n"
        )
        assert not _m.check_removed(doc, doc.read_text())

    def test_a_planned_heading_governs_its_section(self, tmp_path: Path) -> None:
        """A doc that honestly labels something unbuilt must not be penalised for saying so."""
        doc = tmp_path / "d.md"
        doc.write_text(
            "## CLI access (planned)\n\n```bash\nswarmkit notifications --last 10\n```\n"
        )
        assert not _m.check_cli(doc, doc.read_text())


class TestTheRepoIsClean:
    def test_no_current_state_doc_contradicts_the_repo(self) -> None:
        """The check CI runs. If this fails, read its output — it names the file and the claim."""
        assert _m.main() == 0, "run `just docs-check` to see the findings"
