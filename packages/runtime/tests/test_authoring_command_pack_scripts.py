"""Unit tests for the bundled author-tools command_pack scripts (#1045 PR 3).

The scripts are what the authoring agent actually calls to touch disk. Each is a
small program that:

* reads JSON from stdin
* resolves the target workspace from ``SWARMKIT_AUTHOR_TARGET_WORKSPACE``
* does its thing
* writes a single JSON line to stdout and exits with a documented code

These tests exercise each script as a subprocess (the way the compiler will call it
once PR 4 wires the shim through ``swarmkit run``) so the stdin/stdout/env contract is
covered end to end. No shortcuts through the Python callables inside the scripts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.authoring._resolver import get_authoring_workspace_path

_PACK = get_authoring_workspace_path() / "command_packs" / "author-tools"


def _run(
    script: str,
    *,
    stdin: str,
    target: Path | None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if target is not None:
        env["SWARMKIT_AUTHOR_TARGET_WORKSPACE"] = str(target)
    else:
        env.pop("SWARMKIT_AUTHOR_TARGET_WORKSPACE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_PACK / script)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _parse(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.stdout.strip(), f"empty stdout; stderr={result.stderr}"
    first_line = result.stdout.strip().splitlines()[-1]
    parsed: dict[str, Any] = json.loads(first_line)
    return parsed


# ----- write-file -------------------------------------------------------------------


def test_write_file_lands_in_target(tmp_path: Path) -> None:
    stdin = json.dumps(
        {
            "files": {
                "topologies/hello.yaml": "apiVersion: swarmkit/v1\nkind: Topology\n",
                "skills/hello.yaml": "apiVersion: swarmkit/v1\nkind: Skill\n",
            }
        }
    )
    r = _run("write_file.py", stdin=stdin, target=tmp_path)
    assert r.returncode == 0, r.stderr
    out = _parse(r)
    assert set(out["written"]) == {"topologies/hello.yaml", "skills/hello.yaml"}
    assert (tmp_path / "topologies" / "hello.yaml").is_file()
    assert (tmp_path / "skills" / "hello.yaml").is_file()


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.yaml",
        "topologies/../../escape.yaml",
        "/etc/passwd",
        "~/.ssh/authorized_keys",
        "credentials.env",  # outside the allowed roots
        "random/path/under/workspace.yaml",
    ],
)
def test_write_file_refuses_scope_violations(tmp_path: Path, bad_path: str) -> None:
    stdin = json.dumps({"files": {bad_path: "x: y\n"}})
    r = _run("write_file.py", stdin=stdin, target=tmp_path)
    assert r.returncode == 3, f"expected write_scope_violation for {bad_path!r}; got {r.returncode}"
    out = _parse(r)
    assert out["error"] == "write_scope_violation"


def test_write_file_requires_target_env(tmp_path: Path) -> None:
    stdin = json.dumps({"files": {"topologies/x.yaml": "x: y\n"}})
    r = _run("write_file.py", stdin=stdin, target=None)
    assert r.returncode == 4
    assert _parse(r)["error"].startswith("target_workspace")


def test_write_file_refuses_empty_content(tmp_path: Path) -> None:
    stdin = json.dumps({"files": {"topologies/x.yaml": "   \n"}})
    r = _run("write_file.py", stdin=stdin, target=tmp_path)
    assert r.returncode == 2
    assert _parse(r)["error"] == "empty_content"


# ----- read-workspace ---------------------------------------------------------------


def test_read_workspace_inventories_target(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text("id: demo\n", encoding="utf-8")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "topologies" / "one.yaml").write_text("x: y\n", encoding="utf-8")
    (tmp_path / "topologies" / "two.yaml").write_text("x: y\n", encoding="utf-8")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "s.yaml").write_text("x: y\n", encoding="utf-8")

    r = _run("read_workspace.py", stdin="{}", target=tmp_path)
    assert r.returncode == 0, r.stderr
    out = _parse(r)
    assert out["workspace_yaml"] == "id: demo\n"
    assert out["topologies"] == ["one", "two"]
    assert out["skills"] == ["s"]
    assert out["archetypes"] == []
    assert out["funnels"] == []


def test_read_workspace_requires_target_env() -> None:
    r = _run("read_workspace.py", stdin="{}", target=None)
    assert r.returncode == 4


# ----- validate-workspace -----------------------------------------------------------


def test_validate_workspace_reports_valid(tmp_path: Path) -> None:
    # Minimum valid workspace: workspace.yaml only, no agents/skills — the resolver
    # accepts an empty-but-well-formed workspace.
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata:\n"
        "  id: empty-ws\n  name: Empty\n  description: An empty workspace.\n",
        encoding="utf-8",
    )
    r = _run("validate_workspace.py", stdin="{}", target=tmp_path)
    assert r.returncode == 0, r.stderr
    out = _parse(r)
    assert out["valid"] is True
    assert out["counts"]["topologies"] == 0


def test_validate_workspace_reports_errors(tmp_path: Path) -> None:
    # Missing workspace.yaml → resolver complains; the script surfaces it structurally.
    r = _run("validate_workspace.py", stdin="{}", target=tmp_path)
    assert r.returncode == 0
    out = _parse(r)
    assert out["valid"] is False
    assert out["errors"], "expected at least one error for a workspace without workspace.yaml"


# ----- search-skills-catalogue ------------------------------------------------------


def test_search_skills_catalogue_direct_hit(tmp_path: Path) -> None:
    r = _run("search_skills_catalogue.py", stdin=json.dumps({"query": "gmail"}), target=tmp_path)
    assert r.returncode == 0, r.stderr
    out = _parse(r)
    assert out["matches"], "gmail query should produce at least one match"
    first = out["matches"][0]
    assert first["id"] == "gmail"
    assert first["install"] == "swarmkit skill add bundle:gmail"
    assert first["requires_credentials"] == ["gmail"]


def test_search_skills_catalogue_keyword_hit(tmp_path: Path) -> None:
    r = _run(
        "search_skills_catalogue.py",
        stdin=json.dumps({"query": "pdf"}),
        target=tmp_path,
    )
    assert r.returncode == 0
    out = _parse(r)
    ids = [m["id"] for m in out["matches"]]
    assert "markitdown" in ids, "pdf should surface markitdown via the keyword list"


def test_search_skills_catalogue_empty_query(tmp_path: Path) -> None:
    r = _run(
        "search_skills_catalogue.py",
        stdin=json.dumps({"query": "   "}),
        target=tmp_path,
    )
    assert r.returncode == 2
    assert _parse(r)["error"] == "empty_query"


def test_search_skills_catalogue_no_matches(tmp_path: Path) -> None:
    r = _run(
        "search_skills_catalogue.py",
        stdin=json.dumps({"query": "nonsense-no-bundle-fits"}),
        target=tmp_path,
    )
    assert r.returncode == 0
    out = _parse(r)
    assert out["matches"] == []
