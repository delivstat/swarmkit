"""Command packs — configuration, argv substitution, bounds, and the governance gate.

The load-bearing test in this file is :class:`TestArgvIsDataNeverSyntax`. Everything else protects
a behaviour; that one protects the property the whole design rests on.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import pytest
from swarmkit_runtime.commands import (
    BinaryRequirement,
    CommandExecutionError,
    CommandPackConfig,
    CommandPackError,
    CommandSpecConfig,
    action_for,
    audit_payload,
    build_argv,
    check_command_permission,
    check_requirements,
    parse_command_packs,
    resolve_env,
    run_command,
)
from swarmkit_runtime.commands._config import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
)
from swarmkit_runtime.governance import PolicyDecision


def spec(**kw: object) -> CommandSpecConfig:
    base: dict[str, object] = {"command_id": "echo", "argv": ("echo", "{value}")}
    base.update(kw)
    return CommandSpecConfig(**base)  # type: ignore[arg-type]


def pack(**kw: object) -> CommandPackConfig:
    base: dict[str, object] = {"pack_id": "p", "commands": {"echo": spec()}}
    base.update(kw)
    return CommandPackConfig(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# The security property
# --------------------------------------------------------------------------------------------


class TestArgvIsDataNeverSyntax:
    """A substituted value must arrive as exactly one argument, whatever is in it.

    There is no shell, and argv elements are filled one-for-one, so this holds structurally rather
    than by escaping. These cases exist so that a future refactor toward ``shell=True`` or a
    string-joined command line fails loudly here instead of silently.
    """

    @pytest.mark.parametrize(
        "hostile",
        [
            "; rm -rf /",
            "| cat /etc/passwd",
            "$(whoami)",
            "`id`",
            "&& curl evil.example",
            "a b c",
            "line1\nline2",
            "--not-a-flag",
            "'quoted'",
            '"double"',
            "\\backslash",
            "*",
        ],
    )
    def test_hostile_value_is_one_argument(self, hostile: str) -> None:
        argv = build_argv(spec(), {"value": hostile})
        assert argv == ("echo", hostile)
        assert len(argv) == 2

    def test_hostile_value_reaches_the_process_intact(self) -> None:
        """End to end: the process receives the metacharacters as literal text."""
        p = pack(
            commands={
                "show": CommandSpecConfig(
                    command_id="show",
                    argv=(
                        sys.executable,
                        "-c",
                        "import sys; print(sys.argv[1], end='')",
                        "{value}",
                    ),
                    effects="read",
                )
            }
        )
        result = asyncio.run(
            run_command(p, p.commands["show"], arguments={"value": "; rm -rf / && echo pwned"})
        )
        assert result.stdout == "; rm -rf / && echo pwned"

    def test_credential_in_argv_is_rejected_at_parse(self) -> None:
        """The schema forbids it; this is the second lock, on the constructor."""

        @dataclass
        class RawSpec:
            id: str
            argv: list[str]

        @dataclass
        class RawPack:
            id: str
            commands: list[RawSpec]

        raw = RawPack(id="gh", commands=[RawSpec(id="x", argv=["gh", "--token", "{credential.t}"])])
        with pytest.raises(CommandPackError, match="may not be substituted into argv"):
            parse_command_packs([raw])


class TestSubstitution:
    def test_missing_placeholder_is_an_error_not_an_empty_string(self) -> None:
        """A command silently missing an argument runs against the wrong thing,
        and reports success while doing it."""
        with pytest.raises(CommandExecutionError, match=r"expects \['value'\]"):
            build_argv(spec(), {})

    def test_extra_arguments_are_ignored(self) -> None:
        assert build_argv(spec(), {"value": "x", "unused": "y"}) == ("echo", "x")

    def test_non_string_values_are_stringified(self) -> None:
        assert build_argv(spec(), {"value": 42}) == ("echo", "42")

    def test_placeholders_are_discovered_from_every_argv_element(self) -> None:
        s = spec(argv=("tool", "--a={a}", "{b}"))
        assert s.placeholders == frozenset({"a", "b"})


# --------------------------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------------------------


class TestBounds:
    def test_timeout_kills_a_hanging_command(self) -> None:
        p = pack(
            timeout_seconds=1,
            commands={
                "hang": CommandSpecConfig(
                    command_id="hang",
                    argv=(sys.executable, "-c", "import time; time.sleep(30)"),
                    effects="read",
                )
            },
        )
        with pytest.raises(CommandExecutionError, match="exceeded its 1s timeout"):
            asyncio.run(run_command(p, p.commands["hang"], arguments={}))

    def test_output_over_the_ceiling_fails_rather_than_truncating(self) -> None:
        """A truncated result read as complete is worse than a failure:
        nothing downstream can tell the difference."""
        p = pack(
            max_output_bytes=1024,
            commands={
                "flood": CommandSpecConfig(
                    command_id="flood",
                    argv=(sys.executable, "-c", "print('x' * 100000)"),
                    effects="read",
                )
            },
        )
        with pytest.raises(CommandExecutionError, match="ceiling"):
            asyncio.run(run_command(p, p.commands["flood"], arguments={}))

    def test_defaults_are_bounded_not_infinite(self) -> None:
        p = pack()
        assert p.timeout_for("echo") == DEFAULT_TIMEOUT_SECONDS
        assert p.max_output_bytes == DEFAULT_MAX_OUTPUT_BYTES

    def test_per_command_timeout_override(self) -> None:
        p = pack(timeout_seconds=30, timeout_overrides={"echo": 5})
        assert p.timeout_for("echo") == 5
        assert p.timeout_for("other") == 30

    def test_nonzero_exit_is_a_failure_carrying_stderr(self) -> None:
        p = pack(
            commands={
                "boom": CommandSpecConfig(
                    command_id="boom",
                    argv=(
                        sys.executable,
                        "-c",
                        "import sys; print('bad', file=sys.stderr); sys.exit(3)",
                    ),
                    effects="read",
                )
            }
        )
        with pytest.raises(CommandExecutionError, match="exited 3: bad"):
            asyncio.run(run_command(p, p.commands["boom"], arguments={}))

    def test_missing_executable_names_the_binary(self) -> None:
        p = pack(
            commands={
                "nope": CommandSpecConfig(
                    command_id="nope", argv=("definitely-not-a-real-binary-xyz",), effects="read"
                )
            }
        )
        with pytest.raises(CommandExecutionError, match="definitely-not-a-real-binary-xyz"):
            asyncio.run(run_command(p, p.commands["nope"], arguments={}))


# --------------------------------------------------------------------------------------------
# Tiers, effects, and the gate
# --------------------------------------------------------------------------------------------


class FakeGovernance:
    """Records what it was asked, allows everything."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def evaluate_action(
        self,
        *,
        agent_id: str,
        action: str,
        scopes_required: frozenset[str],
        context: dict[str, object] | None = None,
    ) -> PolicyDecision:
        self.calls.append(
            {"agent_id": agent_id, "action": action, "scopes": scopes_required, "context": context}
        )
        return PolicyDecision(allowed=True, reason="fake: allowed", tier=1)


def gate(
    p: CommandPackConfig | None,
    s: CommandSpecConfig | None,
    gov: object = None,
    **kw: object,
) -> tuple[bool, str]:
    return asyncio.run(
        check_command_permission(
            p,
            s,
            gov,  # type: ignore[arg-type]
            agent_id=kw.pop("agent_id", "a"),  # type: ignore[arg-type]
            pack_id=kw.pop("pack_id", "p"),  # type: ignore[arg-type]
            command_id=kw.pop("command_id", "echo"),  # type: ignore[arg-type]
            **kw,  # type: ignore[arg-type]
        )
    )


class TestPermissionTiers:
    def test_tier_resolution_mirrors_the_mcp_table(self) -> None:
        p = pack(permission="cautious", permission_overrides={"echo": "strict"})
        assert p.permission_for("echo") == "strict"
        assert p.permission_for("anything-else") == "cautious"

    def test_open_skips_governance_entirely(self) -> None:
        gov = FakeGovernance()
        allowed, _ = gate(pack(permission="open"), spec(), gov)
        assert allowed
        assert gov.calls == []

    def test_readonly_denies_a_declared_write(self) -> None:
        p = pack(permission="readonly", commands={"echo": spec(effects="write")})
        allowed, reason = gate(p, p.commands["echo"], FakeGovernance())
        assert not allowed
        assert "readonly" in reason

    def test_readonly_allows_a_declared_read(self) -> None:
        p = pack(permission="readonly", commands={"echo": spec(effects="read")})
        allowed, _ = gate(p, p.commands["echo"], FakeGovernance())
        assert allowed

    def test_readonly_is_decided_by_declaration_not_by_the_name(self) -> None:
        """Issue #825: the MCP path substring-scans the action, so `truncate_table` slips through
        and `send_query` is denied. A declared effect has no such gap."""
        destructive = CommandSpecConfig(command_id="truncate_table", argv=("t",), effects="write")
        p = pack(
            permission="readonly",
            commands={"truncate_table": destructive},
        )
        allowed, _ = gate(p, destructive, FakeGovernance(), command_id="truncate_table")
        assert not allowed, "a write must be denied however innocuous its name looks"

        harmless = CommandSpecConfig(command_id="send_query", argv=("q",), effects="read")
        p2 = pack(permission="readonly", commands={"send_query": harmless})
        allowed2, _ = gate(p2, harmless, FakeGovernance(), command_id="send_query")
        assert allowed2, "a read must be allowed however write-ish its name reads"

    def test_undeclared_effects_defaults_to_write_so_it_fails_closed(self) -> None:
        assert spec().effects == "write"
        p = pack(permission="readonly", commands={"echo": spec()})
        allowed, _ = gate(p, p.commands["echo"], FakeGovernance())
        assert not allowed


class TestGateContract:
    def test_unknown_pack_is_refused_by_name(self) -> None:
        allowed, reason = gate(None, None, FakeGovernance(), pack_id="ghost")
        assert not allowed
        assert "ghost" in reason

    def test_unknown_command_lists_what_the_pack_has(self) -> None:
        allowed, reason = gate(pack(), None, FakeGovernance(), command_id="nope")
        assert not allowed
        assert "echo" in reason

    def test_action_string_is_a_sibling_of_the_mcp_one(self) -> None:
        assert action_for("json-tools", "query") == "command:call:json-tools:query"

    def test_audit_payload_carries_structure_not_a_parsed_string(self) -> None:
        p = pack()
        assert audit_payload(p, spec(effects="read")) == {
            "provider": "command",
            "container": "p",
            "member": "echo",
            "effects": "read",
        }

    def test_scopes_and_structure_reach_governance(self) -> None:
        gov = FakeGovernance()
        allowed, _ = gate(pack(), spec(), gov, scopes=frozenset({"workspace:read"}))
        assert allowed
        call = gov.calls[0]
        assert call["action"] == "command:call:p:echo"
        assert call["scopes"] == frozenset({"workspace:read"})
        context = call["context"]
        assert isinstance(context, dict)
        assert context["provider"] == "command"
        assert context["effects"] == "write"
        assert context["server_permission"] == "cautious"


# --------------------------------------------------------------------------------------------
# Load-time requirements
# --------------------------------------------------------------------------------------------


class TestRequirements:
    def test_missing_binary_is_reported_by_name(self) -> None:
        p = pack(requires=(BinaryRequirement(binary="definitely-not-real-xyz"),))
        problems = check_requirements({"p": p})
        assert len(problems) == 1
        assert "definitely-not-real-xyz" in problems[0]

    def test_present_binary_passes(self) -> None:
        p = pack(requires=(BinaryRequirement(binary=sys.executable.split("/")[-1]),))
        assert check_requirements({"p": p}) in ([], [])

    def test_version_constraint_is_checked(self) -> None:
        p = pack(requires=(BinaryRequirement(binary="python3", version=">=99.0"),))
        problems = check_requirements({"p": p})
        assert problems and "99.0" in problems[0]

    def test_satisfied_version_constraint_passes(self) -> None:
        p = pack(requires=(BinaryRequirement(binary="python3", version=">=3.0"),))
        assert check_requirements({"p": p}) == []


class TestEnvironment:
    def test_credentials_resolve_into_env_only(self) -> None:
        p = pack(env={"TOKEN": "{credential.gh}"})
        env = resolve_env(p, {"gh": "s3cret"})
        assert env["TOKEN"] == "s3cret"

    def test_unknown_credential_becomes_empty_not_the_literal_placeholder(self) -> None:
        p = pack(env={"TOKEN": "{credential.absent}"})
        assert resolve_env(p, {})["TOKEN"] == ""


class TestOutputParsing:
    def test_json_output_is_parsed(self) -> None:
        p = pack(
            commands={
                "j": CommandSpecConfig(
                    command_id="j",
                    argv=(sys.executable, "-c", "print('{\"a\": 1}')"),
                    effects="read",
                    parse="json",
                )
            }
        )
        assert asyncio.run(run_command(p, p.commands["j"], arguments={})).parsed == {"a": 1}

    def test_invalid_json_under_a_json_declaration_is_an_error(self) -> None:
        p = pack(
            commands={
                "j": CommandSpecConfig(
                    command_id="j",
                    argv=(sys.executable, "-c", "print('not json')"),
                    effects="read",
                    parse="json",
                )
            }
        )
        with pytest.raises(CommandExecutionError, match="invalid JSON"):
            asyncio.run(run_command(p, p.commands["j"], arguments={}))

    def test_lines_output_drops_blanks(self) -> None:
        p = pack(
            commands={
                "l": CommandSpecConfig(
                    command_id="l",
                    argv=(sys.executable, "-c", "print('a\\n\\nb')"),
                    effects="read",
                    parse="lines",
                )
            }
        )
        assert asyncio.run(run_command(p, p.commands["l"], arguments={})).parsed == ["a", "b"]
