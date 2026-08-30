"""The bundled packs under `reference/command-packs/` are real, valid, and say what they claim.

A reference artifact nobody executes is a reference artifact that rots. These load each pack the
way a user's workspace would, and assert the properties its README promises — so a pack that stops
being read-only, or stops validating, fails here rather than in someone's workspace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime.commands import parse_command_packs
from swarmkit_schema import validate
from swarmkit_schema.models.workspace import SwarmKitWorkspace

PACK_DIR = Path(__file__).parents[3] / "reference" / "command-packs"
PACK_FILES = sorted(PACK_DIR.glob("*.yaml"))


def _as_workspace(pack: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "swarmkit/v1",
        "kind": "Workspace",
        "metadata": {"id": "ref", "name": "ref"},
        "command_packs": [pack],
    }


def _load(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text())
    return dict(doc["command_pack"])


def test_there_is_at_least_one_reference_pack() -> None:
    """Otherwise every test below vacuously passes."""
    assert PACK_FILES, f"no reference packs found under {PACK_DIR}"


@pytest.mark.parametrize("path", PACK_FILES, ids=lambda p: p.name)
class TestEveryReferencePack:
    def test_validates_as_a_workspace_command_pack(self, path: Path) -> None:
        validate("workspace", _as_workspace(_load(path)))

    def test_parses_into_a_runtime_config(self, path: Path) -> None:
        ws = SwarmKitWorkspace.model_validate(_as_workspace(_load(path)))
        packs = parse_command_packs(ws.command_packs)
        assert len(packs) == 1
        pack = next(iter(packs.values()))
        assert pack.commands, "a pack with no commands exposes nothing"

    def test_the_file_id_matches_the_pack_id(self, path: Path) -> None:
        """A file named one thing and declaring another is how the wrong pack gets copied."""
        doc = yaml.safe_load(path.read_text())
        assert doc["command_pack"]["id"] == path.stem
        assert doc["metadata"]["id"] == path.stem

    def test_declares_the_binaries_it_needs(self, path: Path) -> None:
        """Checked at workspace load, so a pack that omits it fails late and unhelpfully."""
        pack = next(
            iter(
                parse_command_packs(
                    SwarmKitWorkspace.model_validate(_as_workspace(_load(path))).command_packs
                ).values()
            )
        )
        assert pack.requires, f"{path.name} declares no `requires:`"

    def test_every_command_declares_its_effects(self, path: Path) -> None:
        """Undeclared would default to `write` and be silently excluded from `pack:` grants —
        working, but not what a reference pack should model."""
        raw = _load(path)
        for command in raw["commands"]:
            assert "effects" in command, f"{path.name}: '{command['id']}' declares no effects"

    def test_no_credential_reaches_argv(self, path: Path) -> None:
        raw = _load(path)
        for command in raw["commands"]:
            for part in command["argv"]:
                assert "{credential." not in part, f"{path.name}: '{command['id']}'"

    def test_a_readonly_pack_is_read_only_throughout(self, path: Path) -> None:
        """The README claims `readonly` is enforceable here rather than aspirational. It is only
        true if every command actually declares `read`."""
        pack = next(
            iter(
                parse_command_packs(
                    SwarmKitWorkspace.model_validate(_as_workspace(_load(path))).command_packs
                ).values()
            )
        )
        if pack.permission != "readonly":
            pytest.skip(f"{path.name} is not a readonly pack")
        writes = [c.command_id for c in pack.commands.values() if c.effects != "read"]
        assert not writes, f"{path.name} is readonly but declares writes: {writes}"

    def test_is_documented_in_the_readme(self, path: Path) -> None:
        readme = (PACK_DIR / "README.md").read_text()
        assert path.name in readme, (
            f"{path.name} is not listed in reference/command-packs/README.md"
        )
