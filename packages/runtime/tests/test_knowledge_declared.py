"""A `knowledge:` block loads, and is reported until something builds it (#1138, PR 1a).

The schema accepts knowledge bases before `KnowledgeService` exists (PR 1b). A block that was
accepted, validated and then did nothing would be the declared-but-unreachable failure
(design/details/declared-but-unreachable.md), so each declared base appears in `swarmkit
validate`'s reachability report as reached by nothing, and `--require` fails on it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.cli import app
from swarmkit_runtime.resolver import resolve_workspace
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


def _ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns(".swarmkit", "__pycache__"))
    doc = yaml.safe_load((root / "workspace.yaml").read_text())
    doc["knowledge"] = {
        "bases": [
            {
                "id": "handbook",
                "kind": "rag",
                "description": "Team policies.",
                "sources": [{"path": "docs/**/*.md"}],
            }
        ]
    }
    (root / "workspace.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


def test_a_knowledge_block_loads(tmp_path: Path) -> None:
    ws = resolve_workspace(_ws(tmp_path))
    assert ws.raw.knowledge is not None
    (base,) = ws.raw.knowledge.bases or []
    assert str(getattr(base.id, "root", base.id)) == "handbook"


def test_a_declared_base_is_reported_as_not_built_yet(tmp_path: Path) -> None:
    report = WorkspaceRuntime.from_workspace_path(_ws(tmp_path)).reachability()
    (item,) = [u for u in report.unreachable if u.declaration.kind == "knowledge_base"]
    assert item.declaration.key == "handbook"
    assert "does not build yet (#1138)" in item.line()


def test_validate_shows_it_and_require_fails_on_it(tmp_path: Path) -> None:
    root = _ws(tmp_path)
    shown = CliRunner().invoke(app, ["validate", str(root)])
    assert "knowledge_base 'handbook'" in shown.stdout
    required = CliRunner().invoke(app, ["validate", str(root), "--require"])
    assert required.exit_code != 0
