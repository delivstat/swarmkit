"""Per-run execution context — the run-state directory and the current parent agent.

Both were previously process-wide (a literal ``.swarmkit/run-state/current`` directory and
a module-global parent pointer), so two runs in one process — exactly the ``serve`` / web-UI
target — would read/write each other's ``tasks.json`` / ``scope.json`` and corrupt each
other's trace parent-attribution. Here they are ``ContextVar``s: ``asyncio.create_task`` /
``gather`` copy the context, so each run (and each delegated child) sees its own values.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

# The current run's state directory, namespaced by run id. ``None`` outside a run.
_run_dir_var: ContextVar[Path | None] = ContextVar("_run_dir", default=None)
# The parent agent of the node currently executing (for trace attribution). ``None`` at root.
_parent_agent_var: ContextVar[str | None] = ContextVar("_parent_agent", default=None)


def run_state_dir(workspace_root: Path | None = None) -> Path:
    """The current run's state directory (created if needed). Inside a ``run_context`` this
    is the run-scoped dir; outside one it falls back to the legacy
    ``<workspace_root or cwd>/.swarmkit/run-state/current`` for back-compat."""
    d = _run_dir_var.get()
    if d is None:
        base = workspace_root if workspace_root is not None else Path.cwd()
        d = base / ".swarmkit" / "run-state" / "current"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def run_context(workspace_root: Path, run_id: str) -> Iterator[Path]:
    """Scope the run-state directory to ``<workspace_root>/.swarmkit/run-state/<run_id>`` for
    the current async context (and the tasks it spawns), isolating concurrent runs."""
    short = "".join(c for c in (run_id or "")[:12] if c.isalnum() or c in "-_") or "current"
    d = workspace_root / ".swarmkit" / "run-state" / short
    d.mkdir(parents=True, exist_ok=True)
    token = _run_dir_var.set(d)
    try:
        yield d
    finally:
        _run_dir_var.reset(token)


_current_agent_var: ContextVar[str | None] = ContextVar("swarmkit_current_agent", default=None)


def current_agent() -> str | None:
    """The agent whose node is executing in the current context, or None outside a node.

    Set at node entry (LangGraph runs each node in its own task with a copied context, so it
    never leaks to a sibling or the parent). The recorder that writes `swarmkit debug`'s ring
    buffer reads it: the node's FIRST model call happens before `set_parent_agent`, so the parent
    variable alone left every opening prompt unattributed.
    """
    return _current_agent_var.get()


def set_current_agent(agent_id: str) -> None:
    _current_agent_var.set(agent_id)


def current_parent_agent() -> str | None:
    """The parent agent of the node executing in the current context (``None`` at root)."""
    return _parent_agent_var.get()


def set_parent_agent(agent_id: str | None) -> object:
    """Set the current-context parent agent, returning a token for ``reset_parent_agent``.
    (Token API, not a context manager, so callers with many early returns stay readable.)"""
    return _parent_agent_var.set(agent_id)


def reset_parent_agent(token: object) -> None:
    from contextvars import Token  # noqa: PLC0415

    if isinstance(token, Token):
        _parent_agent_var.reset(token)


# The run's governed-memory store (design/details/governed-memory.md), or None when the workspace
# has none. Installed by the runtime for the run's context so a `persistence` skill executed
# anywhere in the tool loop can write through the governed path without the store being threaded
# through six call sites — the same reason the circuit-breaker tracker is a ContextVar.
_governed_memory_var: ContextVar[Any] = ContextVar("swarmkit_governed_memory", default=None)


def current_governed_memory() -> Any:
    """The governed-memory store for the current run, or None."""
    return _governed_memory_var.get()


def set_run_governed_memory(store: Any) -> Token[Any]:
    return _governed_memory_var.set(store)


def reset_run_governed_memory(token: Token[Any]) -> None:
    _governed_memory_var.reset(token)


# The run's skill gap log (design §12): fed by the tool loop when an agent calls a tool it does
# not hold. Installed by the runtime alongside the governed-memory store, for the same reason.
_gap_log_var: ContextVar[Any] = ContextVar("swarmkit_gap_log", default=None)


def current_gap_log() -> Any:
    return _gap_log_var.get()


def set_run_gap_log(log: Any) -> Token[Any]:
    return _gap_log_var.set(log)


def reset_run_gap_log(token: Token[Any]) -> None:
    _gap_log_var.reset(token)
