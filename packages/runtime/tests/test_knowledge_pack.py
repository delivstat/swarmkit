"""Tests for ``swarmkit knowledge-pack`` (M1, task #24).

See ``design/details/knowledge-pack-cli.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import swarmkit_runtime.cli as cli_module
from swarmkit_runtime.cli import app
from swarmkit_runtime.cli._knowledge import build_pack, find_repo_root, is_historical, note_status
from typer.testing import CliRunner

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = REPO_ROOT / "examples" / "hello-swarm"


# ---- corpus shape unit tests -----------------------------------------


def _fake_repo(tmp_path: Path) -> Path:
    """Minimal repo-shaped directory the generator can chew through."""
    (tmp_path / "README.md").write_text("# Fake\n\nA test fixture.\n")
    (tmp_path / "CLAUDE.md").write_text("# Invariants\n\n1. test.\n")
    (tmp_path / "llms.txt").write_text("# index\n")
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "SwarmKit-Design-v0.6.md").write_text("# Design v0.6\n")
    (tmp_path / "design" / "IMPLEMENTATION-PLAN.md").write_text("# Plan\n")
    details = tmp_path / "design" / "details"
    details.mkdir()
    (details / "topology-schema-v1.md").write_text("# Topology schema\n")
    (details / "README.md").write_text("# details index\n")  # excluded
    (details / "_template.md").write_text("# template\n")  # excluded
    notes = tmp_path / "docs" / "notes"
    notes.mkdir(parents=True)
    (notes / "usability-first.md").write_text("# Usability\n")
    guides = tmp_path / "docs" / "guides"
    guides.mkdir(parents=True)
    (guides / "authoring-harness-adapters.md").write_text("# Authoring a harness adapter\n")
    schemas_dir = tmp_path / "packages" / "schema" / "schemas"
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "topology.schema.json").write_text('{"$id": "topology"}\n')
    fixtures_dir = tmp_path / "packages" / "schema" / "tests" / "fixtures" / "topology"
    fixtures_dir.mkdir(parents=True)
    (fixtures_dir / "valid.yaml").write_text("apiVersion: swarmkit/v1\nkind: Topology\n")
    pkg_claude = tmp_path / "packages" / "runtime"
    pkg_claude.mkdir(parents=True)
    (pkg_claude / "CLAUDE.md").write_text("# runtime invariants\n")
    reference = tmp_path / "docs" / "site" / "reference"
    reference.mkdir(parents=True)
    (reference / "cli.md").write_text("# CLI\n\n| `swarmkit run` | run |\n")
    (reference / "http-api.md").write_text("# HTTP API\n\n| `POST` | `/run/{topology_name}` |\n")
    # A note whose subject was removed: front matter says so and names its replacement.
    (details / "bundled-thing.md").write_text(
        "---\ntitle: Bundled thing\nstatus: superseded\n"
        "superseded_by: extracting-the-thing.md\n---\n\n"
        "# Bundled thing\n\nHow the thing worked.\n"
    )
    (details / "extracting-the-thing.md").write_text(
        "# Taking the thing out\n\n**Status:** implemented — removed in 1.189.0.\n"
    )
    return tmp_path


def test_pack_includes_every_section(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    pack = build_pack(repo, now=datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC))

    for heading in (
        "## Project overview",
        "## Reference",
        "## Authoritative design",
        "## Per-feature design notes",
        "## Cross-cutting notes",
        "## How-to guides",
        "## Per-package invariants",
        "## Canonical schemas",
        "## Schema fixtures",
        "## Historical design notes",
    ):
        assert heading in pack

    # Excluded files really are excluded.
    assert "design/details/README.md" not in pack
    assert "_template.md" not in pack

    # Timestamp is pinned so tests are stable.
    assert "2026-04-22T10:00:00Z" in pack


def test_the_reference_is_in_the_pack_and_the_reader_is_told_to_trust_it(tmp_path: Path) -> None:
    """Before this the pack had no complete list of commands or routes — only the prose in
    llms.txt — which is the first thing an LLM asked to drive SwarmKit needs. The generated
    reference is the one part of the corpus that cannot be stale, and the preamble says so."""
    pack = build_pack(_fake_repo(tmp_path))
    assert "### `docs/site/reference/cli.md`" in pack
    assert "### `docs/site/reference/http-api.md`" in pack
    assert "the Reference section says what shipped" in pack
    assert pack.index("## Reference") < pack.index("## Authoritative design")


def test_a_superseded_note_lands_last_under_a_banner(tmp_path: Path) -> None:
    """A reader that stops early has only read things that exist. The note stays — the reasoning
    is the record — but after everything current, and the banner says not to answer 'how do I'
    from it."""
    pack = build_pack(_fake_repo(tmp_path))
    current = pack.index("## Per-feature design notes")
    historical = pack.index("## Historical design notes")
    assert current < pack.index("### `design/details/extracting-the-thing.md`") < historical
    assert pack.index("### `design/details/bundled-thing.md`") > historical
    assert "describe things SwarmKit no longer has" in pack
    # Not duplicated into the current section.
    assert pack.count("### `design/details/bundled-thing.md`") == 1


def test_status_is_read_from_front_matter_or_a_status_line() -> None:
    assert note_status("---\ntitle: x\nstatus: Superseded\n---\n# x\n") == "superseded"
    assert note_status("# x\n\n**Status:** implemented — removed in 1.189.0.\n") == (
        "implemented — removed in 1.189.0."
    )
    assert note_status("# x\n\nno status here\n") == ""
    assert is_historical("---\nstatus: superseded\n---\n")
    assert is_historical("---\nstatus: removed\n---\n")
    assert not is_historical("---\nstatus: implemented\n---\n")
    assert not is_historical("# x\n")


def test_lean_pack_is_the_one_that_fits(tmp_path: Path) -> None:
    """Lean keeps what is needed to USE SwarmKit — overview, reference, design doc, guides, notes,
    schemas — and drops the per-feature notes (three quarters of the full pack), the roadmap and
    the fixtures. The header says which pack it is and roughly how many tokens."""
    repo = _fake_repo(tmp_path)
    pack = build_pack(repo, lean=True)
    for heading in (
        "## Project overview",
        "## Reference",
        "## Authoritative design",
        "## Canonical schemas",
    ):
        assert heading in pack
    for gone in (
        "## Per-feature design notes",
        "## Historical design notes",
        "## Schema fixtures",
        "design/IMPLEMENTATION-PLAN.md",
        "### `design/details/",
    ):
        assert gone not in pack
    assert "lean pack" in pack and "k tokens" in pack
    full = build_pack(repo)
    assert "full pack" in full and len(pack) < len(full)


def test_pack_no_fixtures_flag(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    pack = build_pack(repo, include_fixtures=False)
    assert "## Schema fixtures" not in pack
    assert "topology/valid.yaml" not in pack


def test_json_and_yaml_files_are_fenced(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    pack = build_pack(repo)
    assert "```json" in pack
    assert "```yaml" in pack


# ---- workspace overlay -----------------------------------------------


def test_workspace_overlay_includes_yaml_and_validation() -> None:
    pack = build_pack(
        REPO_ROOT,
        workspace=EXAMPLE / "workspace",
        include_fixtures=False,
        now=datetime(2026, 4, 22, tzinfo=UTC),
    )
    assert "## Current workspace" in pack
    assert "workspace/workspace.yaml" in pack
    assert "workspace/topologies/hello.yaml" in pack
    assert "## Validation output (`ok`)" in pack
    assert "hello-swarm" in pack


def test_workspace_overlay_includes_validation_error_block() -> None:
    pack = build_pack(
        REPO_ROOT,
        workspace=EXAMPLE / "workspace-broken",
        include_fixtures=False,
    )
    assert "## Validation output (`errors`)" in pack
    assert "agent.unknown-archetype" in pack
    assert "greter" in pack


# ---- find_repo_root --------------------------------------------------


def test_find_repo_root_walks_up_to_markers() -> None:
    # From inside the runtime package, find_repo_root() should land on
    # the real repo root — i.e. the parent of this test file up two levels.
    assert find_repo_root() == REPO_ROOT


def test_find_repo_root_returns_none_when_markers_missing(tmp_path: Path) -> None:
    # Empty directory has neither CLAUDE.md nor design/.
    assert find_repo_root(tmp_path) is None


# ---- CLI integration -------------------------------------------------


def test_cli_emits_pack_to_stdout() -> None:
    result = runner.invoke(app, ["knowledge-pack", str(EXAMPLE / "workspace")])
    assert result.exit_code == 0
    assert "# SwarmKit Knowledge Pack" in result.stdout
    assert "## Current workspace" in result.stdout


def test_cli_output_file(tmp_path: Path) -> None:
    out = tmp_path / "pack.md"
    result = runner.invoke(app, ["knowledge-pack", "-o", str(out)])
    assert result.exit_code == 0
    # Nothing on stdout when -o is used.
    assert result.stdout == ""
    assert out.read_text(encoding="utf-8").startswith("# SwarmKit Knowledge Pack")


def test_cli_missing_workspace_path_exits_usage(tmp_path: Path) -> None:
    result = runner.invoke(app, ["knowledge-pack", str(tmp_path / "nope")])
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "not found" in combined


def test_cli_broken_workspace_still_exits_zero() -> None:
    # Knowledge-pack is not validation — an invalid workspace is part of
    # the "I'm stuck, help me" flow and must not fail the pack itself.
    result = runner.invoke(app, ["knowledge-pack", str(EXAMPLE / "workspace-broken")])
    assert result.exit_code == 0
    assert "agent.unknown-archetype" in result.stdout


def test_cli_no_fixtures_flag_shrinks_pack() -> None:
    with_fixtures = runner.invoke(app, ["knowledge-pack"])
    without_fixtures = runner.invoke(app, ["knowledge-pack", "--no-fixtures"])
    assert with_fixtures.exit_code == 0
    assert without_fixtures.exit_code == 0
    assert len(without_fixtures.stdout) < len(with_fixtures.stdout)
    assert "## Schema fixtures" not in without_fixtures.stdout


# ---- no source checkout ----------------------------------------------


def test_cli_errors_when_not_in_source_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate "not in a source checkout" by making the resolver return
    # None even though the tests themselves run from inside the repo.
    monkeypatch.setattr(cli_module, "find_repo_root", lambda: None)
    result = runner.invoke(app, ["knowledge-pack"])
    assert result.exit_code == 2
    combined = (result.stdout or "") + (result.stderr or "")
    assert "source checkout" in combined
