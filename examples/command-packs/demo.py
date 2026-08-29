#!/usr/bin/env python3
"""Command packs, demonstrated against the real runtime — not described.

Run: uv run python examples/command-packs/demo.py

Five claims, each shown rather than asserted:

  1. A command runs and returns its output.
  2. A hostile argument value is data. `; rm -rf /` reaches the process as one argv
     entry, printed back verbatim, having executed nothing.
  3. `permission: readonly` denies a declared write — from the declaration, not from a
     guess about the command's name (issue #825).
  4. Bounds fail loudly. Over the output ceiling is an error, never a partial result
     that reads as complete.
  5. `requires` is checked at load and names the missing binary.

Every command here runs `python3`, so the demo needs nothing installed. The example
workspace beside it uses `jq`, which is the realistic case — and claim 5 shows what
happens on a machine that does not have it.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from swarmkit_runtime.commands import (
    BinaryRequirement,
    CommandExecutionError,
    CommandPackConfig,
    CommandSpecConfig,
    build_argv,
    check_command_permission,
    check_requirements,
    run_command,
)

HERE = Path(__file__).parent
GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"

#: Echoes argv[1:] back, one entry per line, so what the process actually received is visible.
ECHO_ARGV = "import sys; print(chr(10).join(sys.argv[1:]), end='')"


def head(n: int, title: str) -> None:
    print(f"\n{BOLD}{n}. {title}{OFF}")


def ok(msg: str) -> None:
    print(f"   {GREEN}✓{OFF} {msg}")


def bad(msg: str) -> None:
    print(f"   {RED}✗{OFF} {msg}")


def shown(label: str, value: str) -> None:
    print(f"   {DIM}{label}:{OFF} {value}")


READ_PACK = CommandPackConfig(
    pack_id="text-tools",
    permission="readonly",
    timeout_seconds=10,
    max_output_bytes=1_048_576,
    commands={
        "echo": CommandSpecConfig(
            command_id="echo",
            argv=(sys.executable, "-c", ECHO_ARGV, "{value}"),
            effects="read",
        )
    },
)

MISNAMED_WRITE = CommandPackConfig(
    pack_id="looks-harmless",
    permission="readonly",
    commands={
        # Named so that nothing in it matches the MCP path's write-signal list
        # ("create","delete","update","write","modify","edit","insert","drop","push","send").
        # A substring scan lets this through. A declared effect does not.
        "truncate_table": CommandSpecConfig(
            command_id="truncate_table",
            argv=(sys.executable, "-c", "pass"),
            effects="write",
        )
    },
)


async def main() -> int:
    # ---- 1 ------------------------------------------------------------------
    head(1, "A command runs")
    spec = READ_PACK.commands["echo"]
    result = await run_command(READ_PACK, spec, arguments={"value": "hello"})
    shown("argv", repr(list(result.argv)))
    ok(f"stdout: {result.stdout!r}")

    # ---- 2 ------------------------------------------------------------------
    head(2, "A hostile value is data, never syntax")
    hostile = "; rm -rf / && echo pwned"
    argv = build_argv(spec, {"value": hostile})
    shown("argv", repr(list(argv)))
    if len(argv) != 4 or argv[-1] != hostile:
        bad("the value was split or altered on the way to argv")
        return 1
    ok("the whole string is ONE argv entry — not split on spaces, not re-parsed")

    received = await run_command(READ_PACK, spec, arguments={"value": hostile})
    shown("what the process received", repr(received.stdout))
    if received.stdout != hostile:
        bad("the process saw something other than the literal value")
        return 1
    ok("the process got the literal text; there is no shell for `rm` to reach")

    # ---- 3 ------------------------------------------------------------------
    head(3, "readonly denies a declared write, whatever it is called")
    allowed, reason = await check_command_permission(
        MISNAMED_WRITE,
        MISNAMED_WRITE.commands["truncate_table"],
        None,
        agent_id="analyst",
        pack_id=MISNAMED_WRITE.pack_id,
        command_id="truncate_table",
    )
    shown("command", "truncate_table (effects: write)")
    shown("name-scan verdict", "no write-signal substring matches — would have been ALLOWED")
    if allowed:
        bad("a declared write got through readonly")
        return 1
    ok(f"denied on the declaration: {reason}")

    # ---- 4 ------------------------------------------------------------------
    head(4, "Bounds fail loudly rather than truncating")
    flood = CommandPackConfig(
        pack_id="flood",
        max_output_bytes=512,
        commands={
            "big": CommandSpecConfig(
                command_id="big",
                argv=(sys.executable, "-c", "print('x' * 50000)"),
                effects="read",
            )
        },
    )
    try:
        await run_command(flood, flood.commands["big"], arguments={})
    except CommandExecutionError as exc:
        ok(str(exc))
        ok("a partial result would have been indistinguishable from a short one")
    else:
        bad("the ceiling did not fire")
        return 1

    # ---- 5 ------------------------------------------------------------------
    head(5, "requires is checked at load, and names what is missing")
    jq_pack = CommandPackConfig(
        pack_id="json-tools",
        requires=(BinaryRequirement(binary="jq", version=">=1.6"),),
        commands={"query": CommandSpecConfig(command_id="query", argv=("jq",), effects="read")},
    )
    problems = check_requirements({"json-tools": jq_pack})
    if problems:
        for p in problems:
            ok(p)
        ok("the workspace beside this demo would refuse to load, naming the binary")
    else:
        ok("jq is present and satisfies >=1.6 — the example workspace loads here")

    print(f"\n{GREEN}{BOLD}All five claims demonstrated.{OFF}")
    print(f"{DIM}Example workspace: {HERE / 'workspace'}{OFF}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
