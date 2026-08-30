"""Bulk grants — `pack:<id>` and `server:<id>` in an agent's skill list.

The property under test is the one a bulk grant can quietly lose: **a pack grant carries reads
only.** Adding a read command to a pack flows through to everyone holding it, which is the
ergonomics packs exist for; adding a write command does not, because silently widening what an
agent can already do is the failure mode of every bulk grant.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime.commands._synthesis import synthetic_skill_id
from swarmkit_runtime.errors import ResolutionErrors
from swarmkit_runtime.resolver import resolve_workspace

BIN = Path(sys.executable).name


def _ws(
    tmp_path: Path,
    *,
    commands: list[dict[str, Any]] | None = None,
    grants: list[str],
    packs: list[dict[str, Any]] | None = None,
    skills: dict[str, dict[str, Any]] | None = None,
) -> Path:
    root = tmp_path / "ws"
    (root / "topologies").mkdir(parents=True, exist_ok=True)
    (root / "skills").mkdir(parents=True, exist_ok=True)
    declared = (
        packs
        if packs is not None
        else [
            {
                "id": "tools",
                "commands": commands
                or [{"id": "read-one", "argv": [BIN, "-c", "pass"], "effects": "read"}],
            }
        ]
    )
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "g", "name": "g"},
                "command_packs": declared,
            }
        )
    )
    for sid, impl in (skills or {}).items():
        (root / "skills" / f"{sid}.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "swarmkit/v1",
                    "kind": "Skill",
                    "metadata": {"id": sid, "name": sid, "description": "A skill for the test."},
                    "category": "capability",
                    "implementation": impl,
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


READ = {"id": "look", "argv": [BIN, "-c", "pass"], "effects": "read"}
WRITE = {"id": "change", "argv": [BIN, "-c", "pass"], "effects": "write"}


class TestPackGrantCarriesReadsOnly:
    def test_a_pack_grant_expands_to_every_read_command(self, tmp_path: Path) -> None:
        root = _ws(
            tmp_path,
            commands=[READ, {"id": "look-again", "argv": [BIN, "-c", "pass"], "effects": "read"}],
            grants=["pack:tools"],
        )
        assert _granted(root) == ["tools-look", "tools-look-again"]

    def test_a_write_command_is_excluded_from_the_bulk_form(self, tmp_path: Path) -> None:
        """The safety property. Adding `change` to this pack must not widen anyone holding it."""
        root = _ws(tmp_path, commands=[READ, WRITE], grants=["pack:tools"])
        granted = _granted(root)
        assert granted == ["tools-look"]
        assert "tools-change" not in granted

    def test_a_write_command_is_available_when_named(self, tmp_path: Path) -> None:
        root = _ws(tmp_path, commands=[READ, WRITE], grants=["pack:tools", "tools-change"])
        assert _granted(root) == ["tools-look", "tools-change"]

    def test_undeclared_effects_is_write_and_so_is_excluded(self, tmp_path: Path) -> None:
        """Fails closed at the grant layer too, not only at the permission tier."""
        root = _ws(
            tmp_path,
            commands=[READ, {"id": "vague", "argv": [BIN, "-c", "pass"]}],
            grants=["pack:tools"],
        )
        assert _granted(root) == ["tools-look"]

    def test_naming_a_command_twice_grants_it_once(self, tmp_path: Path) -> None:
        root = _ws(tmp_path, commands=[READ], grants=["pack:tools", "tools-look"])
        assert _granted(root) == ["tools-look"]


class TestServerGrant:
    def test_expands_to_every_skill_targeting_that_server(self, tmp_path: Path) -> None:
        root = _ws(
            tmp_path,
            grants=["server:fs"],
            skills={
                "fs-read": {"type": "mcp_tool", "server": "fs", "tool": "read"},
                "fs-write": {"type": "mcp_tool", "server": "fs", "tool": "write"},
                "other": {"type": "mcp_tool", "server": "elsewhere", "tool": "x"},
            },
        )
        assert _granted(root) == ["fs-read", "fs-write"]

    def test_does_not_filter_by_effects_because_mcp_does_not_declare_them(
        self, tmp_path: Path
    ) -> None:
        """Asymmetric with `pack:` on purpose. An MCP tool has no declared effect to filter on
        (issue #825), so a server grant cannot make the same promise — and pretending it could
        would be worse than the asymmetry."""
        root = _ws(
            tmp_path,
            grants=["server:fs"],
            skills={"fs-delete-everything": {"type": "mcp_tool", "server": "fs", "tool": "rm"}},
        )
        assert _granted(root) == ["fs-delete-everything"]


class TestLoudFailures:
    def test_a_pack_grant_matching_nothing_is_an_error(self, tmp_path: Path) -> None:
        """Not an empty set. An agent silently granted no tools looks exactly like one whose
        model chose not to use them, and that only surfaces much later as a puzzling transcript."""
        root = _ws(tmp_path, grants=["pack:ghost"])
        with pytest.raises(ResolutionErrors) as exc:
            resolve_workspace(root)
        codes = [e.code for e in exc.value.errors]
        assert "agent.unknown-bulk-grant" in codes
        assert "ghost" in exc.value.errors[0].message

    def test_the_error_lists_what_is_available(self, tmp_path: Path) -> None:
        root = _ws(tmp_path, grants=["pack:ghost"])
        with pytest.raises(ResolutionErrors) as exc:
            resolve_workspace(root)
        assert "tools" in (exc.value.errors[0].suggestion or "")

    def test_an_empty_target_is_an_error(self, tmp_path: Path) -> None:
        root = _ws(tmp_path, grants=["pack:"])
        with pytest.raises(ResolutionErrors):
            resolve_workspace(root)

    def test_a_synthetic_id_colliding_with_a_real_skill_is_an_error(self, tmp_path: Path) -> None:
        """Rather than one shadowing the other, which would make it ambiguous which is called."""
        root = _ws(
            tmp_path,
            commands=[READ],
            grants=["tools-look"],
            skills={"tools-look": {"type": "llm_prompt", "prompt": "hello"}},
        )
        with pytest.raises(ResolutionErrors) as exc:
            resolve_workspace(root)
        assert "command-pack.id-collision" in [e.code for e in exc.value.errors]


class TestSynthesis:
    def test_the_synthetic_id_is_pack_then_command(self) -> None:
        assert synthetic_skill_id("json-tools", "query") == "json-tools-query"

    def test_inputs_are_derived_from_the_argv_placeholders(self, tmp_path: Path) -> None:
        root = _ws(
            tmp_path,
            commands=[
                {
                    "id": "look",
                    "argv": [BIN, "-c", "pass", "{filter}", "{file}"],
                    "effects": "read",
                }
            ],
            grants=["pack:tools"],
        )
        skill = resolve_workspace(root).skills["tools-look"]
        inputs = skill.raw.inputs
        assert inputs is not None
        dumped = inputs if isinstance(inputs, dict) else inputs.model_dump(exclude_none=True)
        props = dumped["properties"]
        required = dumped["required"]
        assert set(props) == {"filter", "file"}
        assert sorted(required) == ["file", "filter"]

    def test_the_description_states_the_effect_for_the_model_to_read(self, tmp_path: Path) -> None:
        """The tier enforces it; the description is how the model knows before choosing."""
        root = _ws(tmp_path, commands=[READ, WRITE], grants=["pack:tools", "tools-change"])
        ws = resolve_workspace(root)
        assert "Read-only" in (ws.skills["tools-look"].raw.metadata.description or "")
        assert "modifies data" in (ws.skills["tools-change"].raw.metadata.description or "")


class TestCommandSkillsBecomeTools:
    """The gap that made a command skill declared-but-unreachable.

    `_executable_types` listed only llm_prompt and mcp_tool, so a command skill resolved onto an
    agent and then produced no tool — the model never saw it. Every unit test passed, because they
    exercised `execute_skill` directly and never the path that decides what the model is offered.
    This is exactly the anatomy `design/details/declared-but-unreachable.md` is about.
    """

    def test_a_command_skill_produces_a_tool(self, tmp_path: Path) -> None:
        from swarmkit_runtime.langgraph_compiler._prompts import _build_tools  # noqa: PLC0415

        root = _ws(tmp_path, commands=[READ], grants=["pack:tools"])
        agent = resolve_workspace(root).topologies["t"].root
        assert [t.name for t in _build_tools(agent)] == ["tools-look"]

    def test_the_tool_schema_carries_the_argv_placeholders(self, tmp_path: Path) -> None:
        """So the model learns the arguments from the schema rather than by failing a call."""
        from swarmkit_runtime.langgraph_compiler._prompts import _build_tools  # noqa: PLC0415

        root = _ws(
            tmp_path,
            commands=[{"id": "look", "argv": [BIN, "-c", "p", "{a}", "{b}"], "effects": "read"}],
            grants=["pack:tools"],
        )
        agent = resolve_workspace(root).topologies["t"].root
        tool = next(t for t in _build_tools(agent) if t.name == "tools-look")
        assert sorted(tool.input_schema["properties"]) == ["a", "b"]
        assert sorted(tool.input_schema["required"]) == ["a", "b"]

    def test_a_skill_reachable_twice_still_produces_one_tool(self, tmp_path: Path) -> None:
        """Duplicate tool names are rejected by every provider, so this is a hard error."""
        from swarmkit_runtime.langgraph_compiler._prompts import _build_tools  # noqa: PLC0415

        root = _ws(tmp_path, commands=[READ], grants=["pack:tools", "tools-look"])
        agent = resolve_workspace(root).topologies["t"].root
        names = [t.name for t in _build_tools(agent)]
        assert names == ["tools-look"]
        assert len(names) == len(set(names))


class TestShippedExample:
    def test_the_example_expands_as_documented(self) -> None:
        root = Path(__file__).parents[3] / "examples" / "command-packs" / "workspace"
        ws = resolve_workspace(root)
        agents = {a.id: [s.id for s in a.skills] for a in ws.topologies["inspect"].root.children}
        assert agents["analyst"] == ["json-tools-keys", "json-tools-query"]
        assert agents["editor"] == [
            "json-tools-keys",
            "json-tools-query",
            "json-editing-rewrite",
        ]
        assert "json-editing-rewrite" not in agents["analyst"]
