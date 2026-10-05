"""Smoke test for `reference/workspaces/author/` — the Dot Author surface.

Narrow by design: the Author wraps an existing topology as a Dot. It does NOT author
topologies; that is `swarmkit author`'s job. These tests pin the wrapper's commit path
and its refusal to pretend topologies into existence.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

AUTHOR_WORKSPACE = Path(__file__).resolve().parents[3] / "reference/workspaces/author"
CREATE_DOT = AUTHOR_WORKSPACE / "command_packs/author-tools/create_dot.py"


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


def test_create_dot_wraps_an_existing_topology(tmp_path: Path) -> None:
    """create_dot.py writes the Dot entry only when the referenced topology exists.

    The Dot Author's commit path is: user confirms topology exists → create-dot. This
    pins the happy path end-to-end.
    """
    target = tmp_path / "ws"
    (target / "topologies").mkdir(parents=True)
    (target / "topologies/smoke-topology.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Topology\nmetadata:\n  name: smoke-topology\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(CREATE_DOT),
            "smoke-dot",
            "Smoke Dot",
            "r",
            "hi",
            "sunrise",
            "smoke-topology",
            "[]",
        ],
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
    assert payload["dot"]["topology"] == "smoke-topology"
    assert payload["topology_path"].endswith("smoke-topology.yaml")


def test_create_dot_refuses_when_topology_missing(tmp_path: Path) -> None:
    """create_dot.py exits 3 with topology_missing when the referenced topology is not
    there — the whole point of the delegation is that this script cannot author."""
    target = tmp_path / "ws"
    (target / "topologies").mkdir(parents=True)
    result = subprocess.run(
        [
            sys.executable,
            str(CREATE_DOT),
            "ghost-dot",
            "Ghost Dot",
            "r",
            "hi",
            "sunrise",
            "ghost-topology",
            "[]",
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "AUTHOR_TARGET_WORKSPACE": str(target)},
        check=False,
    )
    assert result.returncode == 3
    assert "topology_missing" in result.stdout


@pytest.mark.parametrize(
    ("argv", "expected_error_substr", "expected_exit"),
    [
        (["ok"], "missing_field", 2),
        (["BAD-ID", "x", "x", "x", "x", "t", "[]"], "invalid_id", 2),
    ],
)
def test_create_dot_rejects_bad_input(
    tmp_path: Path,
    argv: list[str],
    expected_error_substr: str,
    expected_exit: int,
) -> None:
    target = tmp_path / "ws"
    (target / "topologies").mkdir(parents=True)
    (target / "topologies/t.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Topology\nmetadata:\n  name: t\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(CREATE_DOT), *argv],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "AUTHOR_TARGET_WORKSPACE": str(target)},
        check=False,
    )
    assert result.returncode == expected_exit
    assert expected_error_substr in result.stdout
