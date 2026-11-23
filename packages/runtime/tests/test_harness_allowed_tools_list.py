"""`allowed_tools` written as a YAML list reaches the harness as a tool list (#1099).

Tutorial 17 writes the grant as a list: `allowed_tools: [Read, Edit, "Bash(git *)"]`. The runtime
read it with `str(...)`, so Claude Code was launched with
`--allowedTools "['Read', 'Edit', 'Bash(git *)']"`, which matches no tool name. A grant meant to
narrow the agent denied it every tool instead. Both forms now give the same comma-separated grant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    DeclarativeExecutor,
    SandboxHandle,
    TaskSpec,
    allowed_tools_entries,
    load_adapter_specs,
)

LIST = ["Read", "Edit", "Bash(git *)"]
STRING = "Read,Edit,Bash(git *)"


def _grant_arg(kind: str, flag: str, allowed: Any, mcp_tools: tuple[str, ...] = ()) -> str:
    executor = DeclarativeExecutor(load_adapter_specs()[kind], config={"allowed_tools": allowed})
    argv, _ = executor._launch(
        TaskSpec(statement="do it", mcp_tools=mcp_tools),
        SandboxHandle(root=Path("/tmp/sbx")),
        BudgetEnvelope(),
        None,
        (),
        None,
    )
    return argv[argv.index(flag) + 1]


@pytest.mark.parametrize(
    ("kind", "flag"), [("claude-code", "--allowedTools"), ("copilot", "--available-tools")]
)
def test_a_list_and_a_string_give_the_same_grant(kind: str, flag: str) -> None:
    assert _grant_arg(kind, flag, LIST) == STRING
    assert _grant_arg(kind, flag, STRING) == STRING


def test_the_grant_is_never_a_python_repr() -> None:
    assert "[" not in _grant_arg("claude-code", "--allowedTools", LIST)


def test_gateway_tools_join_a_list_grant_without_duplicates() -> None:
    granted = _grant_arg(
        "claude-code",
        "--allowedTools",
        [*LIST, "mcp__swarmkit__docs__search"],
        mcp_tools=("docs__search", "docs__read"),
    )
    assert granted.split(",") == [
        "Read",
        "Edit",
        "Bash(git *)",
        "mcp__swarmkit__docs__search",
        "mcp__swarmkit__docs__read",
    ]


@pytest.mark.parametrize(
    ("value", "entries"),
    [
        (LIST, LIST),
        (STRING, LIST),
        ("Read, Edit , Bash(git *)", LIST),  # spaces around commas
        (["Read", "", None, " Edit "], ["Read", "Edit"]),
        (None, []),
        ("", []),
        ([], []),
    ],
)
def test_entries_are_read_from_either_form(value: Any, entries: list[str]) -> None:
    assert allowed_tools_entries(value) == entries


def test_no_grant_stays_no_grant() -> None:
    executor = DeclarativeExecutor(load_adapter_specs()["claude-code"], config={})
    argv, _ = executor._launch(
        TaskSpec(statement="x"),
        SandboxHandle(root=Path("/tmp/s")),
        BudgetEnvelope(),
        None,
        (),
        None,
    )
    assert "--allowedTools" not in argv
