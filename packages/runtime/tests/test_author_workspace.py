"""Smoke test for `reference/workspaces/author/` — the reusable authoring surface.

Every reference workspace has a smoke test that loads + compiles it without executing a
run (packages/runtime/CLAUDE.md). This one additionally pins:

- `swarmkit validate` passes on the workspace.
- The `create-dot` script accepts a valid Dot spec and writes a topology YAML that
  itself validates — proving the author's end-to-end commit path is sound.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

AUTHOR_WORKSPACE = Path(__file__).resolve().parents[3] / "reference/workspaces/author"


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke the installed `swarmkit` entrypoint from .venv/bin, same path CI uses."""
    bin_dir = Path(sys.executable).parent
    return subprocess.run(
        [str(bin_dir / "swarmkit"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_author_workspace_validates() -> None:
    """swarmkit validate must pass. If this fails, the workspace is shipping broken."""
    result = _run_cli(["validate", str(AUTHOR_WORKSPACE)])
    assert "no errors" in result.stdout or result.returncode == 0, (
        f"author workspace validate failed:\n{result.stdout}\n{result.stderr}"
    )


def test_create_dot_writes_a_valid_topology(tmp_path: Path) -> None:
    """create_dot.py receives a Dot spec, writes a topology YAML, and the YAML validates.

    This is the Author's commit path: whatever the agent sends to the create-dot tool
    must end up as something the runtime can serve. Pins that end-to-end.
    """
    target = tmp_path / "ws"
    (target / "topologies").mkdir(parents=True)
    (target / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata:\n  id: smoke\n  name: smoke\n",
        encoding="utf-8",
    )
    topology_yaml = (
        "apiVersion: swarmkit/v1\n"
        "kind: Topology\n"
        "metadata:\n"
        "  name: smoke-dot\n"
        "  version: 0.1.0\n"
        "agents:\n"
        "  root:\n"
        "    id: root\n"
        "    role: root\n"
        "    model:\n"
        "      provider: openrouter\n"
        "      name: moonshotai/kimi-k2-0905\n"
        "    prompt:\n"
        "      system: smoke\n"
        "    output_schema: null\n"
    )
    spec = {
        "id": "smoke-dot",
        "name": "Smoke Dot",
        "role": "r",
        "greeting": "hi",
        "icon": "sunrise",
        "topology_yaml": topology_yaml,
        "renderers": [],
    }
    script = AUTHOR_WORKSPACE / "command_packs/author-tools/create_dot.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(spec),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "AUTHOR_TARGET_WORKSPACE": str(target)},
        check=False,
    )
    assert result.returncode == 0, (
        f"create-dot failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["id"] == "smoke-dot"
    assert payload["dot"]["topology"] == "smoke-dot"
    assert (
        Path(payload["topology_path"])
        .read_text(encoding="utf-8")
        .startswith("apiVersion: swarmkit/v1")
    )
    validate = _run_cli(["validate", str(target)])
    assert validate.returncode == 0, (
        f"generated workspace fails validate:\n{validate.stdout}\n{validate.stderr}"
    )


@pytest.mark.parametrize(
    ("payload", "expected_error_substr", "expected_exit"),
    [
        ({"id": "ok"}, "missing_field", 2),
        (
            {
                "id": "BAD-ID",
                "name": "x",
                "role": "x",
                "greeting": "x",
                "icon": "x",
                "topology_yaml": "x",
            },
            "invalid_id",
            2,
        ),
    ],
)
def test_create_dot_rejects_bad_input(
    tmp_path: Path, payload: dict[str, str], expected_error_substr: str, expected_exit: int
) -> None:
    target = tmp_path / "ws"
    (target / "topologies").mkdir(parents=True)
    script = AUTHOR_WORKSPACE / "command_packs/author-tools/create_dot.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "AUTHOR_TARGET_WORKSPACE": str(target)},
        check=False,
    )
    assert result.returncode == expected_exit
    assert expected_error_substr in result.stdout
