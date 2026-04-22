"""Tests for ``swarmkit knowledge-pack`` (M1, task #24).

See ``design/details/knowledge-pack-cli.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import swarmkit_runtime.cli as cli_module
from swarmkit_runtime.cli import app
from swarmkit_runtime.cli._knowledge import build_pack, find_repo_root
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
    schemas_dir = tmp_path / "packages" / "schema" / "schemas"
    schemas_dir.mkdir(parents=True)
    (schemas_dir / "topology.schema.json").write_text('{"$id": "topology"}\n')
    fixtures_dir = tmp_path / "packages" / "schema" / "tests" / "fixtures" / "topology"
    fixtures_dir.mkdir(parents=True)
    (fixtures_dir / "valid.yaml").write_text("apiVersion: swarmkit/v1\nkind: Topology\n")
    pkg_claude = tmp_path / "packages" / "runtime"
    pkg_claude.mkdir(parents=True)
    (pkg_claude / "CLAUDE.md").write_text("# runtime invariants\n")
    return tmp_path


def test_pack_includes_every_section(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    pack = build_pack(repo, now=datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC))

    for heading in (
        "## Project overview",
        "## Authoritative design",
        "## Per-feature design notes",
        "## Cross-cutting notes",
        "## Per-package invariants",
        "## Canonical schemas",
        "## Schema fixtures",
    ):
        assert heading in pack

    # Excluded files really are excluded.
    assert "design/details/README.md" not in pack
    assert "_template.md" not in pack

    # Timestamp is pinned so tests are stable.
    assert "2026-04-22T10:00:00Z" in pack


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
