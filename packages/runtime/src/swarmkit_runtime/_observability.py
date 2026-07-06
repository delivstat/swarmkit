"""Observability facade — read-only access to a workspace's run history.

Centralises the ``.swarmkit/`` on-disk layout and the audit-store + JSONL-log read logic that the
observability CLI commands (``logs`` / ``status`` / ``why`` / ``ask`` / ``debug`` / ``trace`` /
``checkpoints``) each re-implemented. The commands keep the presentation (tables, markdown, the
LLM prompts); this owns the data access, so the read paths are defined + tested once instead of
copy-pasted across eight commands. Reached via ``WorkspaceRuntime.observability(path)``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from swarmkit_runtime.governance import AuditEvent


async def _collect(aiter: Any) -> list[Any]:
    """Drain an async iterator of audit events into a list."""
    out: list[Any] = []
    async for item in aiter:
        out.append(item)
    return out


def _run(coro: Any) -> Any:
    """Run a coroutine to completion from sync CLI code, tolerating a closed/absent loop.

    ``asyncio.run`` used elsewhere in a command closes its loop and clears the current one, so a
    plain ``get_event_loop()`` can hand back a closed loop; get-or-create a live one instead.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class Observability:
    """Read-only view of a workspace's ``.swarmkit/`` run history (audit store + JSONL logs)."""

    def __init__(self, workspace_path: Path) -> None:
        self._root = workspace_path.resolve()

    # ---- .swarmkit layout (single source of truth) --------------------------

    @property
    def swarmkit_dir(self) -> Path:
        return self._root / ".swarmkit"

    @property
    def audit_db(self) -> Path:
        return self.swarmkit_dir / "audit.sqlite"

    @property
    def logs_dir(self) -> Path:
        return self.swarmkit_dir / "logs"

    @property
    def traces_dir(self) -> Path:
        return self.swarmkit_dir / "traces"

    @property
    def prompts_db(self) -> Path:
        return self.swarmkit_dir / "prompts.sqlite"

    @property
    def tasks_json(self) -> Path:
        return self.swarmkit_dir / "run-state" / "current" / "tasks.json"

    @property
    def last_thread_file(self) -> Path:
        return self.swarmkit_dir / "state" / "last_thread.txt"

    @property
    def checkpoints_db(self) -> Path:
        return self.swarmkit_dir / "state" / "checkpoints.db"

    # ---- audit store --------------------------------------------------------

    def query_audit(
        self, *, run_id: str | None = None, agent: str | None = None, limit: int
    ) -> list[AuditEvent] | None:
        """Query the SQLite audit store.

        Returns ``None`` when there is no audit store or it is empty — the caller's signal to fall
        back to JSONL logs. Otherwise returns the (possibly empty) matching events. Opening,
        counting, querying, and closing the provider is handled here.
        """
        if not self.audit_db.is_file():
            return None
        from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

        provider = WorkspaceRuntime.audit_provider_for(self._root)

        async def _count_then_query() -> list[AuditEvent] | None:
            # Count + query on one loop so the provider's connection stays on a single loop.
            if await provider.count() == 0:
                return None
            return await _collect(provider.query(run_id=run_id, agent_id=agent, limit=limit))

        try:
            result: list[AuditEvent] | None = _run(_count_then_query())
            return result
        finally:
            provider.close_sync()

    @staticmethod
    def filter_by_run(events: list[AuditEvent], run_filter: str | None) -> list[AuditEvent]:
        """Keep events whose topology_id or run_id contains *run_filter* (all if it's empty)."""
        if not run_filter:
            return list(events)
        return [
            e
            for e in events
            if (e.topology_id and run_filter in e.topology_id)
            or (e.run_id and run_filter in e.run_id)
        ]

    # ---- JSONL run logs -----------------------------------------------------

    def run_log_files(self, *, topology: str | None = None, limit: int | None = None) -> list[Path]:
        """Run-log JSONL files, newest first; optionally filtered by topology prefix + capped."""
        if not self.logs_dir.is_dir():
            return []
        files = sorted(self.logs_dir.glob("*.jsonl"), reverse=True)
        if topology:
            files = [f for f in files if f.name.startswith(f"{topology}-")]
        return files[:limit] if limit is not None else files

    def find_run_log(self, run_id: str) -> Path | None:
        """The newest JSONL run log whose name/stem starts with *run_id* (for the why command)."""
        if not self.logs_dir.is_dir():
            return None
        matches = [
            f
            for f in self.logs_dir.glob("*.jsonl")
            if f.name.startswith(run_id) or f.stem.startswith(run_id)
        ]
        return sorted(matches, reverse=True)[0] if matches else None

    @staticmethod
    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        """Parse a JSONL run-log file into a list of event dicts (blank lines skipped)."""
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").strip().split("\n")
            if line
        ]

    # ---- traces -------------------------------------------------------------

    def find_trace(self, run_id: str) -> Path | None:
        """The trace JSON file matching *run_id* (prefix), or None."""
        if not self.traces_dir.is_dir():
            return None
        matches = sorted(self.traces_dir.glob(f"{run_id}*.json"))
        return matches[0] if matches else None
