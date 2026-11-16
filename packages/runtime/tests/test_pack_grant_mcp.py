"""`pack:<mcp-server>` grants the server's read skills, as `pack:<command-pack>` does (#1123).

Before this, `pack:` resolved only command packs and `pack:workspace`. An MCP server had one bulk
form, `server:<id>`, which carries every skill on the server, writes included — the silent
widening `pack:` was designed to prevent. The `swarmkit-skills` catalogue documents
`pack:<bundle>` as "every read skill, writes never", and a workspace following any bundle README
failed to load with `agent.unknown-bulk-grant`.

The rule is the command packs' rule. A skill is carried when its tool is declared `read` in the
server's `effects` map. A write tool is not, and neither is one the map is silent on: at load time
there is no connection to ask for a `readOnlyHint`, and unknown is never read.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.resolver import resolve_workspace

BIN = Path(sys.executable).name

#: The shape of the catalogue's Kitesurf bundle: three reads, three writes, plus one skill whose
#: tool the effects map does not mention.
EFFECTS = {
    "take_snapshot": "read",
    "take_screenshot": "read",
    "wait_for": "read",
    "navigate_page": "write",
    "click": "write",
    "fill_form": "write",
}
SKILLS = {
    "web-read-page": "take_snapshot",
    "web-screenshot": "take_screenshot",
    "web-wait-for": "wait_for",
    "web-open": "navigate_page",
    "web-click": "click",
    "web-fill-form": "fill_form",
    "web-run-js": "evaluate_script",  # undeclared
}


def _ws(
    tmp_path: Path,
    grants: list[str],
    *,
    effects: dict[str, str] | None = None,
    skills: dict[str, str] | None = None,
    packs: list[dict[str, Any]] | None = None,
    server_id: str = "browser",
) -> Path:
    root = tmp_path / "ws"
    (root / "topologies").mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(parents=True, exist_ok=True)
    workspace: dict[str, Any] = {
        "apiVersion": "swarmkit/v1",
        "kind": "Workspace",
        "metadata": {"id": "g", "name": "g"},
        "mcp_servers": [
            {
                "id": server_id,
                "transport": "stdio",
                "command": [BIN, "-c", "pass"],
                "permission": "cautious",
                "effects": EFFECTS if effects is None else effects,
            }
        ],
    }
    if packs:
        workspace["command_packs"] = packs
    (root / "workspace.yaml").write_text(yaml.safe_dump(workspace))
    for sid, tool in (SKILLS if skills is None else skills).items():
        (root / "skills" / f"{sid}.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "swarmkit/v1",
                    "kind": "Skill",
                    "metadata": {"id": sid, "name": sid, "description": "A skill for the test."},
                    "category": "capability",
                    "implementation": {"type": "mcp_tool", "server": server_id, "tool": tool},
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
                        "skills": grants,
                    }
                },
            }
        )
    )
    return root


def _granted(root: Path) -> list[str]:
    return [s.id for s in resolve_workspace(root).topologies["t"].root.skills]


class TestPackGrantOnAnMcpServer:
    def test_it_carries_the_declared_reads_and_nothing_else(self, tmp_path: Path) -> None:
        root = _ws(tmp_path, ["pack:browser"])
        assert _granted(root) == ["web-read-page", "web-screenshot", "web-wait-for"]

    def test_a_write_tool_is_never_carried(self, tmp_path: Path) -> None:
        """The safety property: adding a write tool to a server cannot widen an existing grant."""
        granted = _granted(_ws(tmp_path, ["pack:browser"]))
        assert not {"web-open", "web-click", "web-fill-form"} & set(granted)

    def test_an_undeclared_tool_is_never_carried(self, tmp_path: Path) -> None:
        """No connection at load time, so no readOnlyHint: unknown is not read."""
        assert "web-run-js" not in _granted(_ws(tmp_path, ["pack:browser"]))

    def test_a_write_is_granted_when_named(self, tmp_path: Path) -> None:
        granted = _granted(_ws(tmp_path, ["pack:browser", "web-open"]))
        assert granted == ["web-read-page", "web-screenshot", "web-wait-for", "web-open"]

    def test_server_grant_still_carries_everything_writes_included(self, tmp_path: Path) -> None:
        assert sorted(_granted(_ws(tmp_path, ["server:browser"]))) == sorted(SKILLS)

    def test_a_server_with_no_declared_reads_is_an_error_not_an_empty_grant(
        self, tmp_path: Path
    ) -> None:
        root = _ws(tmp_path, ["pack:browser"], effects={"navigate_page": "write"})
        with pytest.raises(ResolutionErrors) as exc:
            resolve_workspace(root)
        (err,) = [e for e in exc.value.errors if e.code == "agent.unknown-bulk-grant"]
        assert "none whose tool its `effects` map declares read" in err.message
        assert err.suggestion is not None and "server:browser" in err.suggestion

    def test_an_unknown_name_lists_servers_as_well_as_packs(self, tmp_path: Path) -> None:
        root = _ws(tmp_path, ["pack:nope"])
        with pytest.raises(ResolutionErrors) as exc:
            resolve_workspace(root)
        (err,) = [e for e in exc.value.errors if e.code == "agent.unknown-bulk-grant"]
        assert "command pack or MCP server 'nope'" in err.message
        assert err.suggestion is not None and "browser" in err.suggestion

    def test_a_pack_and_a_server_sharing_an_id_grant_both_sets_of_reads(
        self, tmp_path: Path
    ) -> None:
        packs = [
            {
                "id": "browser",
                "commands": [
                    {"id": "look", "argv": [BIN, "-c", "pass"], "effects": "read"},
                    {"id": "change", "argv": [BIN, "-c", "pass"], "effects": "write"},
                ],
            }
        ]
        granted = _granted(_ws(tmp_path, ["pack:browser"], packs=packs))
        assert granted == ["browser-look", "web-read-page", "web-screenshot", "web-wait-for"]

    def test_the_effect_is_stamped_on_the_resolved_skill(self, tmp_path: Path) -> None:
        skills = resolve_workspace(_ws(tmp_path, ["pack:browser"])).skills
        assert skills["web-read-page"].mcp_effect == "read"
        assert skills["web-click"].mcp_effect == "write"
        assert skills["web-run-js"].mcp_effect is None
