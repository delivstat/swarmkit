"""Configuration objects for command packs.

A pack is the local-command sibling of an ``mcp_server``: it declares a set of commands, a
permission tier, and the bounds every call runs under. The governance model is not mirrored here —
it is reused. ``iam.required_scopes`` on the skill authorizes; the tier resolved here only decides
whether the call reaches governance at all, exactly as it does for MCP.

See ``design/details/command-packs.md``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # version probing at load; argv only, never a shell
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

PermissionTier = Literal["open", "cautious", "strict", "readonly"]
Effects = Literal["read", "write"]

#: Bounds applied when a pack declares none. A command with no ceiling can take a run down with it,
#: so an omitted value means these, never unbounded.
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")
_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class CommandPackError(Exception):
    """A pack could not be resolved — a missing binary, a bad version, an unknown command."""


@dataclass(frozen=True)
class CommandSpecConfig:
    """One command in a pack."""

    command_id: str
    argv: tuple[str, ...]
    #: Declared, never inferred. ``curl`` POSTs and ``jq`` takes ``-i``, so a name proves nothing.
    #: Undeclared means ``write`` so an unclassified command fails closed.
    effects: Effects = "write"
    parse: Literal["text", "json", "lines"] = "text"
    description: str = ""

    @property
    def placeholders(self) -> frozenset[str]:
        """Every ``{name}`` this command's argv expects to be filled."""
        return frozenset(m.group(1) for part in self.argv for m in _PLACEHOLDER.finditer(part))


@dataclass(frozen=True)
class BinaryRequirement:
    binary: str
    version: str = ""


@dataclass(frozen=True)
class CommandPackConfig:
    """A named set of local commands, resolved from a workspace ``command_packs`` entry."""

    pack_id: str
    commands: dict[str, CommandSpecConfig] = field(default_factory=dict)
    requires: tuple[BinaryRequirement, ...] = ()
    permission: PermissionTier = "cautious"
    permission_overrides: dict[str, PermissionTier] = field(default_factory=dict)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    timeout_overrides: dict[str, int] = field(default_factory=dict)
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    credentials_ref: str = ""
    description: str = ""

    def permission_for(self, command_id: str) -> PermissionTier:
        """Per-command override, else the pack default.

        Mirrors ``MCPClientManager.get_permission`` exactly.
        """
        return self.permission_overrides.get(command_id, self.permission)

    def timeout_for(self, command_id: str) -> int:
        return self.timeout_overrides.get(command_id, self.timeout_seconds)


def _expand_vars(value: str) -> str:
    return _ENV_VAR.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _tier(raw: Any, default: PermissionTier = "cautious") -> PermissionTier:
    value = getattr(raw, "value", raw)
    for tier in ("open", "cautious", "strict", "readonly"):
        if value == tier:
            return tier
    return default


def parse_command_packs(packs: Any) -> dict[str, CommandPackConfig]:
    """Convert a workspace's typed ``command_packs`` list into runtime configs.

    Schema validation has already run, so this narrows types rather than re-validating — the one
    exception is the ``{credential.*}`` check, which is asserted in both places deliberately: the
    schema stops it being written, this stops it being constructed any other way.
    """
    if not packs:
        return {}
    configs: dict[str, CommandPackConfig] = {}
    for pack in packs:
        commands: dict[str, CommandSpecConfig] = {}
        for spec in pack.commands:
            argv = tuple(str(a) for a in spec.argv)
            for part in argv:
                if "{credential." in part:
                    msg = (
                        f"command pack '{pack.id}' command '{spec.id}': a credential may not be "
                        f"substituted into argv. Secrets reach a command through the pack's `env` "
                        f"only — in argv they would be model-placeable, land in the audit line "
                        f"recording what ran, and be readable from `ps`."
                    )
                    raise CommandPackError(msg)
            output = getattr(spec, "output", None)
            commands[spec.id] = CommandSpecConfig(
                command_id=spec.id,
                argv=argv,
                effects=_effects(getattr(spec, "effects", None)),
                parse=getattr(getattr(output, "parse", None), "value", None)
                or getattr(output, "parse", None)
                or "text",
                description=getattr(spec, "description", "") or "",
            )
        configs[pack.id] = CommandPackConfig(
            pack_id=pack.id,
            commands=commands,
            requires=tuple(
                BinaryRequirement(binary=r.binary, version=getattr(r, "version", "") or "")
                for r in (getattr(pack, "requires", None) or [])
            ),
            permission=_tier(getattr(pack, "permission", None)),
            permission_overrides={
                k: _tier(v) for k, v in (getattr(pack, "permission_overrides", None) or {}).items()
            },
            timeout_seconds=getattr(pack, "timeout_seconds", None) or DEFAULT_TIMEOUT_SECONDS,
            timeout_overrides=dict(getattr(pack, "timeout_overrides", None) or {}),
            max_output_bytes=getattr(pack, "max_output_bytes", None) or DEFAULT_MAX_OUTPUT_BYTES,
            cwd=getattr(pack, "cwd", "") or "",
            env=dict(getattr(pack, "env", None) or {}),
            credentials_ref=getattr(pack, "credentials_ref", "") or "",
            description=getattr(pack, "description", "") or "",
        )
    return configs


def _effects(raw: Any) -> Effects:
    value = getattr(raw, "value", raw)
    return "read" if value == "read" else "write"


def check_requirements(packs: Mapping[str, CommandPackConfig]) -> list[str]:
    """Verify every declared binary exists and satisfies its version constraint.

    Runs at workspace load rather than at call time. A topology that only runs where a binary
    happens to be installed is weaker portable data than one that does not, and the failure should
    name the binary rather than arrive as an exec error four steps into a run.

    Returns a list of human-readable problems; empty means every pack is runnable here.
    """
    problems: list[str] = []
    for pack in packs.values():
        for req in pack.requires:
            resolved = shutil.which(req.binary)
            if resolved is None:
                problems.append(
                    f"command pack '{pack.pack_id}' requires '{req.binary}', which is not on PATH"
                )
                continue
            if not req.version:
                continue
            found = _probe_version(resolved)
            if found is None:
                problems.append(
                    f"command pack '{pack.pack_id}': could not read a version from "
                    f"'{req.binary} --version' to check against '{req.version}'"
                )
            elif not _satisfies(found, req.version):
                problems.append(
                    f"command pack '{pack.pack_id}' requires {req.binary} "
                    f"{req.version}, found {found}"
                )
    return problems


def _probe_version(executable: str) -> tuple[int, ...] | None:
    try:
        proc = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", f"{proc.stdout}\n{proc.stderr}")
    if match is None:
        return None
    return tuple(int(g) for g in match.groups() if g is not None)


_OPS: tuple[str, ...] = (">=", "<=", "==", ">", "<")


def _satisfies(found: tuple[int, ...], constraint: str) -> bool:
    text = constraint.strip()
    op = next((o for o in _OPS if text.startswith(o)), ">=")
    raw = text[len(op) :].strip() if text.startswith(op) else text
    try:
        want = tuple(int(p) for p in re.findall(r"\d+", raw))
    except ValueError:
        return False
    if not want:
        return False
    width = max(len(found), len(want))
    a = found + (0,) * (width - len(found))
    b = want + (0,) * (width - len(want))
    return {
        ">=": a >= b,
        "<=": a <= b,
        "==": a == b,
        ">": a > b,
        "<": a < b,
    }[op]
