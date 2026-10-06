"""Smoke test for `reference/workspaces/author/` — the reusable authoring surface.

Every reference workspace has a smoke test that loads + compiles it without executing a
run (packages/runtime/CLAUDE.md). After #1045 PR 7 (Dot Author delegation swap) this one
pins:

- `swarmkit validate` passes on the workspace.
- The Dot Author's archetype declares the `author-topology` and `create-dot` skills (so
  the delegation path is wired in metadata, not just the system prompt).
- The `create-dot` script registers a Dot that references a topology written in the
  target workspace — proving the Dot Author's commit path still writes a valid entry.
  The topology itself is now authored by `swarmkit:author:topology`, exercised in that
  bundled workspace's own tests.
- The `create-dot` script refuses to register against a missing topology, so a Dot
  cannot point at nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from swarmkit_runtime.resolver import resolve_workspace

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


def test_dot_author_delegates_through_author_topology() -> None:
    """The Dot Author archetype must declare author-topology + create-dot as its skills.

    This is what makes the delegation structural, not just rhetorical in the prompt. If
    this list changes, the Dot Author stops being a thin wrapper and silently starts
    reinventing the authoring charter again.
    """

    ws = resolve_workspace(AUTHOR_WORKSPACE)
    archetype = ws.archetypes["dot-author"]
    skill_ids = {
        getattr(s, "id", getattr(s, "root", s)) if not isinstance(s, str) else s
        for s in archetype.raw.defaults.skills or []
    }
    assert skill_ids == {"author-topology", "create-dot"}, (
        f"dot-author archetype skills changed to {skill_ids}; "
        "the delegation to swarmkit:author:topology lives in author-topology"
    )
    impl = ws.skills["author-topology"].raw.implementation
    assert getattr(impl, "topology", None) == "swarmkit:author:topology", (
        "author-topology must target the bundled authoring topology"
    )


def test_create_dot_registers_against_existing_topology(tmp_path: Path) -> None:
    """create_dot.py registers a Dot once the topology is in topologies/<id>.yaml.

    After PR 7 the script no longer accepts the raw YAML — swarmkit:author:topology has
    already written it via the IAM-scoped write-file skill. The script's job is to
    confirm the file exists and emit the dots.local.json entry.
    """
    target = tmp_path / "ws"
    (target / "topologies").mkdir(parents=True)
    (target / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata:\n  id: smoke\n  name: smoke\n",
        encoding="utf-8",
    )
    # Simulate what swarmkit:author:topology would have landed.
    (target / "topologies" / "smoke-dot.yaml").write_text(
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
        "    output_schema: null\n",
        encoding="utf-8",
    )
    spec = {
        "id": "smoke-dot",
        "name": "Smoke Dot",
        "role": "r",
        "greeting": "hi",
        "icon": "sunrise",
        "topology": "smoke-dot",
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


def test_create_dot_refuses_missing_topology(tmp_path: Path) -> None:
    """A Dot cannot point at a topology that doesn't exist. If the author hasn't run,
    the registration refuses — pointing a Dot at nothing is worse than no Dot at all.
    """
    target = tmp_path / "ws"
    (target / "topologies").mkdir(parents=True)
    spec = {
        "id": "nope",
        "name": "Nope",
        "role": "r",
        "greeting": "hi",
        "icon": "x",
        "topology": "never-written",
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
    assert result.returncode == 3
    assert "topology_missing" in result.stdout


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
                "topology": "ok",
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
