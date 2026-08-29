"""Execute a command from a pack, under argv-only substitution and declared bounds.

The security property this module exists to hold: **a substituted value is data, never syntax.**
There is no shell, argv elements are substituted one-for-one, and a value containing ``;``, ``|``,
``$(…)``, spaces or newlines arrives at the process as exactly one argument. Nothing downstream has
to remember that, because there is no code path where a value is re-parsed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from swarmkit_runtime.commands._config import _PLACEHOLDER, _expand_vars

if TYPE_CHECKING:
    from swarmkit_runtime.commands._config import CommandPackConfig, CommandSpecConfig


class CommandExecutionError(Exception):
    """A command could not be run, or ran and failed."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    parsed: Any = None


def build_argv(spec: CommandSpecConfig, arguments: dict[str, Any]) -> tuple[str, ...]:
    """Fill ``{name}`` placeholders from ``arguments``, one argv element at a time.

    A placeholder is replaced by the *string value* of the argument and the element stays one
    element. Whitespace in a value does not split it; shell metacharacters in a value are inert
    because no shell ever sees them.

    An unknown placeholder is an error rather than an empty string: a command silently missing an
    argument is a command that runs against the wrong thing and reports success.
    """
    missing = sorted(spec.placeholders - set(arguments))
    if missing:
        msg = (
            f"command '{spec.command_id}' expects {missing} which the skill input did not supply; "
            f"got {sorted(arguments)}"
        )
        raise CommandExecutionError(msg)

    def fill(part: str) -> str:
        return _PLACEHOLDER.sub(lambda m: str(arguments[m.group(1)]), part)

    return tuple(fill(part) for part in spec.argv)


def resolve_env(
    pack: CommandPackConfig, credentials: dict[str, str] | None = None
) -> dict[str, str]:
    """Build the command environment: the parent's, plus the pack's declared entries.

    ``${VAR}`` expands from the runtime process environment; ``{credential.<ref>}`` resolves from
    ``credentials``. This is the only place a secret enters a command — the schema rejects
    ``{credential.*}`` in argv, so there is no second path to keep in step.
    """
    creds = credentials or {}
    env = dict(os.environ)
    for key, raw in pack.env.items():
        value = _expand_vars(raw)
        value = re.sub(
            r"\{credential\.([A-Za-z0-9_.-]+)\}",
            lambda m: creds.get(m.group(1), ""),
            value,
        )
        env[key] = value
    return env


async def run_command(
    pack: CommandPackConfig,
    spec: CommandSpecConfig,
    *,
    arguments: dict[str, Any],
    workspace_root: str = "",
    credentials: dict[str, str] | None = None,
) -> CommandResult:
    """Run one command with the pack's timeout and output ceiling applied."""
    argv = build_argv(spec, arguments)
    timeout = pack.timeout_for(spec.command_id)
    limit = pack.max_output_bytes
    cwd = _expand_vars(pack.cwd) if pack.cwd else (workspace_root or None)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=resolve_env(pack, credentials),
        )
    except FileNotFoundError as exc:
        msg = f"command '{spec.command_id}': executable '{argv[0]}' not found"
        raise CommandExecutionError(msg) from exc
    except OSError as exc:
        msg = f"command '{spec.command_id}': could not start '{argv[0]}': {exc}"
        raise CommandExecutionError(msg) from exc

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        msg = f"command '{spec.command_id}' exceeded its {timeout}s timeout and was killed"
        raise CommandExecutionError(msg) from None

    if len(out) + len(err) > limit:
        # Fail rather than truncate. A truncated result read as complete is the worse outcome —
        # it is indistinguishable from a short one, and every consumer downstream believes it.
        msg = (
            f"command '{spec.command_id}' produced {len(out) + len(err)} bytes, over the pack's "
            f"{limit}-byte ceiling; failing rather than returning a partial result"
        )
        raise CommandExecutionError(msg)

    stdout = out.decode("utf-8", errors="replace")
    stderr = err.decode("utf-8", errors="replace")
    exit_code = proc.returncode if proc.returncode is not None else -1
    if exit_code != 0:
        detail = stderr.strip() or stdout.strip() or "no output"
        msg = f"command '{spec.command_id}' exited {exit_code}: {detail}"
        raise CommandExecutionError(msg)

    return CommandResult(
        argv=argv,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        parsed=_parse(spec, stdout),
    )


def _parse(spec: CommandSpecConfig, stdout: str) -> Any:
    if spec.parse == "json":
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            msg = (
                f"command '{spec.command_id}' declares output.parse: json "
                f"but emitted invalid JSON: {exc}"
            )
            raise CommandExecutionError(msg) from exc
    if spec.parse == "lines":
        return [line for line in stdout.splitlines() if line]
    return stdout
