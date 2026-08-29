"""Command packs at workspace load — resolution, and the two failures that must be loud.

The unit tests in ``test_command_packs.py`` cover the pack in isolation. These cover the seam a
user actually meets: a workspace that declares packs, and the two ways one can be wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime._workspace_runtime import (
    MissingCommandPackError,
    UnrunnableCommandPackError,
    WorkspaceRuntime,
)

PRESENT_BINARY = Path(sys.executable).name


def _workspace(tmp_path: Path, *, packs: list[dict[str, Any]], skill: dict[str, Any]) -> Path:
    root = tmp_path / "ws"
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "topologies").mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "packs", "name": "packs"},
                "command_packs": packs,
            }
        )
    )
    (root / "skills" / "s.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Skill",
                "metadata": {"id": "s", "name": "S", "description": "Runs a command from a pack."},
                "category": "capability",
                "implementation": skill,
                "iam": {"required_scopes": ["workspace:read"]},
                "provenance": {"authored_by": "human", "version": "1.0.0"},
            }
        )
    )
    (root / "topologies" / "t.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Topology",
                "metadata": {"name": "t", "version": "0.1.0"},
                "agents": {
                    "root": {
                        "id": "root",
                        "role": "root",
                        "model": {"provider": "mock", "name": "mock"},
                        "skills": ["s"],
                    }
                },
            }
        )
    )
    return root


def _pack(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "tools",
        "commands": [{"id": "run", "argv": [PRESENT_BINARY, "-c", "pass"], "effects": "read"}],
    }
    base.update(kw)
    return base


def _skill(pack: str = "tools", command: str = "run") -> dict[str, Any]:
    return {"type": "command", "pack": pack, "command": command}


class TestResolution:
    def test_a_declared_pack_resolves_and_reaches_the_runtime(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path, packs=[_pack()], skill=_skill())
        runtime = WorkspaceRuntime.from_workspace_path(root)
        # Reaching into the private attribute deliberately: that the packs reach the
        # runtime at all is the thing under test, and no public surface exposes it yet.
        packs = runtime._command_packs
        assert "tools" in packs
        assert packs["tools"].commands["run"].effects == "read"

    def test_tier_and_bounds_survive_the_round_trip(self, tmp_path: Path) -> None:
        root = _workspace(
            tmp_path,
            packs=[
                _pack(
                    permission="readonly",
                    permission_overrides={"run": "strict"},
                    timeout_seconds=7,
                    timeout_overrides={"run": 3},
                    max_output_bytes=2048,
                )
            ],
            skill=_skill(),
        )
        pack = WorkspaceRuntime.from_workspace_path(root)._command_packs["tools"]
        assert pack.permission == "readonly"
        assert pack.permission_for("run") == "strict"
        assert pack.timeout_for("run") == 3
        assert pack.max_output_bytes == 2048


class TestLoudFailures:
    def test_skill_naming_an_undeclared_pack_fails_at_load(self, tmp_path: Path) -> None:
        root = _workspace(tmp_path, packs=[_pack()], skill=_skill(pack="ghost"))
        with pytest.raises(MissingCommandPackError, match="ghost"):
            WorkspaceRuntime.from_workspace_path(root)

    def test_skill_naming_an_undeclared_command_fails_at_load(self, tmp_path: Path) -> None:
        """The pack exists; the command does not. Resolving the pack alone would let this
        through and fail only when an agent first reached for it."""
        root = _workspace(tmp_path, packs=[_pack()], skill=_skill(command="nope"))
        with pytest.raises(MissingCommandPackError, match="nope"):
            WorkspaceRuntime.from_workspace_path(root)

    def test_missing_binary_fails_at_load_and_names_it(self, tmp_path: Path) -> None:
        """Not at call time. A topology that only runs where a binary happens to exist is
        weaker portable data, and an exec error four steps into a run says nothing useful."""
        root = _workspace(
            tmp_path,
            packs=[_pack(requires=[{"binary": "definitely-not-a-real-binary-xyz"}])],
            skill=_skill(),
        )
        with pytest.raises(UnrunnableCommandPackError, match="definitely-not-a-real-binary-xyz"):
            WorkspaceRuntime.from_workspace_path(root)

    def test_unsatisfiable_version_fails_at_load(self, tmp_path: Path) -> None:
        root = _workspace(
            tmp_path,
            packs=[_pack(requires=[{"binary": PRESENT_BINARY, "version": ">=99.0"}])],
            skill=_skill(),
        )
        with pytest.raises(UnrunnableCommandPackError, match=r"99\.0"):
            WorkspaceRuntime.from_workspace_path(root)

    def test_satisfiable_version_loads(self, tmp_path: Path) -> None:
        root = _workspace(
            tmp_path,
            packs=[_pack(requires=[{"binary": PRESENT_BINARY, "version": ">=3.0"}])],
            skill=_skill(),
        )
        assert WorkspaceRuntime.from_workspace_path(root) is not None


class TestExampleWorkspace:
    def test_the_shipped_example_parses(self) -> None:
        """The example declares jq, which may not be installed here — so this asserts the
        packs parse, not that they are runnable on this machine."""
        from swarmkit_runtime.commands import parse_command_packs  # noqa: PLC0415
        from swarmkit_schema import validate  # noqa: PLC0415

        path = (
            Path(__file__).parents[3]
            / "examples"
            / "command-packs"
            / "workspace"
            / "workspace.yaml"
        )
        raw = yaml.safe_load(path.read_text())
        validate("workspace", raw)

        from swarmkit_schema.models.workspace import SwarmKitWorkspace  # noqa: PLC0415

        packs = parse_command_packs(SwarmKitWorkspace.model_validate(raw).command_packs)
        assert set(packs) == {"json-tools", "json-editing"}
        assert packs["json-tools"].permission == "readonly"
        assert all(c.effects == "read" for c in packs["json-tools"].commands.values())
        assert packs["json-editing"].permission_for("rewrite") == "strict"
        assert packs["json-editing"].commands["rewrite"].effects == "write"
