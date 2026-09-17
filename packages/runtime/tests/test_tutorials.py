"""The guided tutorials are checked, not trusted.

Two guards. Every YAML block in `docs/site/tutorials/*.md` that is a whole artifact (has
`apiVersion` and `kind`) validates against its canonical schema — a tutorial that shows a field
the schema rejects is a bug in the tutorial. And every workspace under `examples/tutorials/`
resolves and runs the topologies its `tutorial.json` names, on the mock provider — the workspace
a level tells the reader to build is the one that is proven to run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
TUTORIALS = REPO_ROOT / "docs" / "site" / "tutorials"
EXAMPLES = REPO_ROOT / "examples" / "tutorials"
SCHEMAS = REPO_ROOT / "packages" / "schema" / "schemas"

KIND_TO_SCHEMA = {
    "Topology": "topology",
    "Workspace": "workspace",
    "Skill": "skill",
    "Archetype": "archetype",
    "Trigger": "trigger",
    "Funnel": "funnel",
    "Contract": "contract",
    "RoleRegistry": "role-registry",
    "ExecutorAdapter": "executor-adapter",
    "ModelProvider": "model-provider",
}

_BLOCK = re.compile(r"```yaml\n(.*?)```", re.S)


def _artifact_blocks() -> list[tuple[str, int, dict[str, Any]]]:
    out: list[tuple[str, int, dict[str, Any]]] = []
    for md in sorted(TUTORIALS.glob("*.md")):
        for i, block in enumerate(_BLOCK.findall(md.read_text(encoding="utf-8"))):
            try:
                doc = yaml.safe_load(block)
            except yaml.YAMLError as exc:
                raise AssertionError(f"{md.name} block {i}: YAML does not parse: {exc}") from exc
            if (
                isinstance(doc, dict)
                and doc.get("apiVersion")
                and doc.get("kind") in KIND_TO_SCHEMA
            ):
                out.append((md.name, i, doc))
    return out


@pytest.mark.parametrize(
    ("name", "index", "doc"),
    [pytest.param(n, i, d, id=f"{n}#{i}:{d['kind']}") for n, i, d in _artifact_blocks()],
)
def test_every_tutorial_artifact_validates(name: str, index: int, doc: dict[str, Any]) -> None:
    schema = json.loads((SCHEMAS / f"{KIND_TO_SCHEMA[doc['kind']]}.schema.json").read_text())
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert not errors, f"{name} block {index}: " + "; ".join(
        f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors[:5]
    )


def _example_dirs() -> list[Path]:
    return sorted(p for p in EXAMPLES.iterdir() if (p / "tutorial.json").exists())


@pytest.mark.parametrize("example", [pytest.param(p, id=p.name) for p in _example_dirs()])
@pytest.mark.asyncio
async def test_every_tutorial_example_runs(
    example: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil  # noqa: PLC0415

    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    spec = json.loads((example / "tutorial.json").read_text())
    if spec.get("skip"):
        pytest.skip(str(spec["skip"]))
    ws = tmp_path / example.name
    shutil.copytree(example, ws, ignore=shutil.ignore_patterns(".swarmkit"))
    rt = WorkspaceRuntime.from_workspace_path(ws)
    for topology, user_input in spec.get("runs", []):
        assert topology in rt.workspace.topologies, f"{example.name}: no topology {topology!r}"
        result = await rt.run(topology, user_input)
        assert result.output, f"{example.name}/{topology}: empty output"
        assert not result.failed, f"{example.name}/{topology}: {result.node_errors}"
