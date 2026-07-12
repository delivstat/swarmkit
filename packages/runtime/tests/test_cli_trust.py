"""`swarmkit trust` CLI — list pending accrual proposals, apply one (edits the archetype's
allowlist), and clear a denial block. The store's semantics are covered in test_trust_accrual; this
covers the human-facing surface + the allowlist edit."""

from __future__ import annotations

from pathlib import Path

import yaml
from swarmkit_runtime.cli import app
from swarmkit_runtime.trust import TrustStore
from typer.testing import CliRunner

runner = CliRunner()


def _archetype(root: Path, arch_id: str, allowed: str | None = None) -> Path:
    directory = root / "archetypes"
    directory.mkdir(parents=True, exist_ok=True)
    config: dict[str, object] = {}
    if allowed is not None:
        config["allowed_tools"] = allowed
    doc = {
        "apiVersion": "swarmkit/v1",
        "kind": "Archetype",
        "metadata": {"id": arch_id, "name": arch_id},
        "role": "worker",
        "defaults": {},
        "executor": {"kind": "claude-code", "ref": "claude-code", "config": config},
    }
    path = directory / f"{arch_id}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def _propose(root: Path, arch: str, cap: str) -> None:
    store = TrustStore(root, threshold=1)
    store.record(arch, cap, True)  # crosses immediately → a proposal


def test_list_shows_pending_proposals(tmp_path: Path) -> None:
    _propose(tmp_path, "coding-worker", "Bash(npm test)")
    result = runner.invoke(app, ["trust", "list", str(tmp_path)])
    assert result.exit_code == 0
    assert "coding-worker" in result.stdout
    assert "Bash(npm test)" in result.stdout


def test_list_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["trust", "list", str(tmp_path)])
    assert result.exit_code == 0
    assert "No trust proposals" in result.stdout


def test_apply_adds_capability_to_archetype_allowlist(tmp_path: Path) -> None:
    _archetype(tmp_path, "coding-worker", allowed="Read")
    _propose(tmp_path, "coding-worker", "Bash(npm test)")

    result = runner.invoke(
        app, ["trust", "apply", "coding-worker", "Bash(npm test)", str(tmp_path)]
    )
    assert result.exit_code == 0

    doc = yaml.safe_load((tmp_path / "archetypes" / "coding-worker.yaml").read_text())
    tools = doc["executor"]["config"]["allowed_tools"]
    assert "Read" in tools and "Bash(npm test)" in tools
    # Applied ⇒ no longer pending.
    assert TrustStore(tmp_path).proposals() == []


def test_apply_seeds_allowlist_when_absent(tmp_path: Path) -> None:
    _archetype(tmp_path, "coding-worker")  # no allowed_tools at all
    _propose(tmp_path, "coding-worker", "Edit")
    result = runner.invoke(app, ["trust", "apply", "coding-worker", "Edit", str(tmp_path)])
    assert result.exit_code == 0
    doc = yaml.safe_load((tmp_path / "archetypes" / "coding-worker.yaml").read_text())
    assert doc["executor"]["config"]["allowed_tools"] == "Edit"


def test_apply_without_proposal_errors(tmp_path: Path) -> None:
    _archetype(tmp_path, "coding-worker")
    result = runner.invoke(app, ["trust", "apply", "coding-worker", "Bash(x)", str(tmp_path)])
    assert result.exit_code == 1


def test_apply_missing_archetype_file_errors(tmp_path: Path) -> None:
    _propose(tmp_path, "ghost", "Bash(x)")  # proposal exists, but no archetype YAML
    result = runner.invoke(app, ["trust", "apply", "ghost", "Bash(x)", str(tmp_path)])
    assert result.exit_code == 1


def test_clear_lifts_block(tmp_path: Path) -> None:
    store = TrustStore(tmp_path, threshold=1)
    store.record("coding-worker", "Bash(deploy)", False)  # blocked
    result = runner.invoke(app, ["trust", "clear", "coding-worker", "Bash(deploy)", str(tmp_path)])
    assert result.exit_code == 0
    # After clearing, the pair accrues again.
    assert store.record("coding-worker", "Bash(deploy)", True) is not None


def test_clear_unknown_pair_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["trust", "clear", "nope", "nope", str(tmp_path)])
    assert result.exit_code == 1
