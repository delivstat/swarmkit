"""Run-side value types for the executor contract (design executor-abstraction §5, §6.0).

The inputs to ``Executor.run()`` — the checkpointed task spec, the provisioned sandbox handle, and
the core-enforced budget envelope — plus the preflight report and a resume token. Behavior (sandbox
provisioning, budget enforcement, adapters) lands in later P2 PRs; this PR defines the shapes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaskSpec:
    """A first-class, checkpointed task handed to ``run()`` — not a bare prompt (§6.0)."""

    statement: str
    pre_answered: Mapping[str, str] = field(default_factory=dict)
    context_files: Mapping[str, str] = field(default_factory=dict)  # e.g. {"CLAUDE.md": "..."}
    mcp_tools: Sequence[str] = field(default_factory=tuple)
    base_ref: str | None = None
    # Path to a generated harness-native MCP config pointing at the governed gateway (set by the
    # harness node once the gateway is up). Exposed as {task.mcp_config} for an adapter to consume.
    mcp_config: str = ""


@dataclass(frozen=True)
class SandboxHandle:
    """A provisioned execution sandbox the adapter runs inside (§6.1). ``root`` is the working dir;
    ``kind`` is the sandbox type. Provisioning/teardown is core's job, not the adapter's.

    ``exec_prefix`` is prepended to the harness argv at the launch chokepoint. Empty for a worktree
    (spawn the harness directly, as today); for a container it is the ``docker run …`` /
    ``podman run …`` wrapper, so the same argv runs inside the container
    (executor-container-sandbox.md)."""

    root: Path
    kind: str = "worktree"
    network: str = "deny"
    exec_prefix: tuple[str, ...] = ()


@dataclass(frozen=True)
class BudgetEnvelope:
    """Core-owned limits enforced uniformly across executor kinds (§6.1). ``None`` = unbounded."""

    max_cost_usd: float | None = None
    max_turns: int | None = None
    max_wall_clock_minutes: float | None = None
    max_idle_seconds: float | None = None


@dataclass(frozen=True)
class PreflightReport:
    """Fail-fast check before any spend (§5): binary present, version ok, credentials resolvable,
    sandbox provisionable."""

    ok: bool
    reason: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumeToken:
    """A vendor session id enabling resume across gates / retries (§5, §6.1)."""

    value: str
