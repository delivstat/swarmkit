"""The harness-build seam, faked deterministically for the slice-7 executor showcase.

The ``developer`` archetype is a **harness executor** (``executor: {kind: harness, ref:
claude-code}``). Here we exercise the *real* bundled ``claude-code`` declarative adapter — its
event-map + interpreter translate a harness's native ``stream-json`` lines into normalized
``ExecEvent``s — but we replace the one impure step (spawning the ``claude`` subprocess) with a
scripted JSONL stream. That is the documented test seam on :class:`DeclarativeExecutor`
(``_open_stream`` is "overridable … tests substitute a scripted line source without a real
binary"): no API keys, no network, no real harness, yet the candidate diff, the tool-call trail,
the usage/cost, and the terminal status all flow through the same code a live run would.

The output is a :class:`CandidateDiff` — the determination artifact the code-review gate judges.

``run_developer_harness`` is the whole seam; ``demo_harness_build.py`` and the slice-7 tests both
drive it, so the fake lives in one place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from swarmkit_runtime.executors._declarative import DeclarativeExecutor, load_adapter_specs
from swarmkit_runtime.executors._events import (
    ExecArtifact,
    ExecEvent,
    ExecResult,
    ExecToolCall,
    ExecUsage,
)
from swarmkit_runtime.executors._run import BudgetEnvelope, SandboxHandle, TaskSpec

DEMO_REPO = Path(__file__).resolve().parent / "fixtures" / "demo-repo"

# The harness reads the approved design and implements the OMS order-status lookup. Two variants:
# the FLAWED first pass (an unguarded dict lookup — the review finding) and the REVISED pass that
# adds the guard. The clean scenario emits the revised diff directly; the route-back scenario emits
# the flawed one, is sent back, then emits the revised one.
DIFF_FLAWED = """\
diff --git a/orders.py b/orders.py
index 1a2b3c4..5d6e7f8 100644
--- a/orders.py
+++ b/orders.py
@@ -26,3 +26,7 @@ def submit_order(order_id: str, items: list[str]) -> Order:
     order = Order(id=order_id, items=items)
     _ORDERS[order_id] = order
     return order
+
+
+def get_order_status(order_id: str) -> str:
+    return _ORDERS[order_id].state
diff --git a/README.md b/README.md
index 9f8e7d6..1c2b3a4 100644
--- a/README.md
+++ b/README.md
@@ -11,3 +11,4 @@ This is a fixture — it is deliberately minimal and is never imported by the run
 ## API

 - `submit_order(order_id, items)` — create an order in the `created` state.
+- `get_order_status(order_id)` — return an order's state.
"""

DIFF_REVISED = """\
diff --git a/orders.py b/orders.py
index 1a2b3c4..5d6e7f8 100644
--- a/orders.py
+++ b/orders.py
@@ -26,3 +26,12 @@ def submit_order(order_id: str, items: list[str]) -> Order:
     order = Order(id=order_id, items=items)
     _ORDERS[order_id] = order
     return order
+
+
+def get_order_status(order_id: str) -> str:
+    \"\"\"Return the current state of an order.
+
+    Raises ``LookupError`` for an unknown order id (guarded lookup).
+    \"\"\"
+    if order_id not in _ORDERS:
+        raise LookupError(f"unknown order: {order_id}")
+    return _ORDERS[order_id].state
diff --git a/README.md b/README.md
index 9f8e7d6..1c2b3a4 100644
--- a/README.md
+++ b/README.md
@@ -11,3 +11,4 @@ This is a fixture — it is deliberately minimal and is never imported by the run
 ## API

 - `submit_order(order_id, items)` — create an order in the `created` state.
+- `get_order_status(order_id)` — return an order's state (guarded).
"""


@dataclass(frozen=True)
class CandidateDiff:
    """The determination artifact of a harness build — a reviewable diff + its manifest.

    ``events`` is the full normalized ``ExecEvent`` stream (as the cockpit / audit would see it);
    the rest are projections the demo and the code-review gate consume.
    """

    diff: str
    status: str
    session_id: str | None
    cost_usd: float | None
    files_changed: tuple[str, ...]
    added: int
    removed: int
    tool_calls: tuple[str, ...]
    manifest: tuple[ExecArtifact, ...]
    events: tuple[ExecEvent, ...] = field(default_factory=tuple)

    @property
    def summary(self) -> str:
        files = ", ".join(self.files_changed)
        return f"{files}  (+{self.added}/-{self.removed})"


class _ScriptedHarness(DeclarativeExecutor):
    """A ``claude-code`` executor whose subprocess is a scripted JSONL stream.

    Only the process launch is faked — the real adapter's ``event_map`` translates every line, so
    the ``ExecEvent`` stream is exactly what a live run would emit.
    """

    def __init__(self, spec: Any, lines: list[str]) -> None:
        super().__init__(spec)
        self._lines = lines

    async def _open_stream(  # type: ignore[override]
        self, argv: list[str], env: Any, cwd: Path, run_id: str
    ) -> Any:
        for line in self._lines:
            yield line if line.endswith("\n") else line + "\n"


def _developer_jsonl(*, session_id: str, diff: str, cost_usd: float, revised: bool) -> list[str]:
    """The harness's native ``stream-json`` transcript for one build: read → edit → test → result.

    Mirrors Claude Code's ``-p --output-format stream-json`` shape so the bundled adapter's real
    event-map does the translating (system→session, assistant text→message, tool_use→tool_call,
    result→usage+result with the diff as ``result``).
    """
    plan = (
        "Revising per the code-review finding: guarding the order-status lookup for unknown ids."
        if revised
        else "Implementing the approved OMS order-status lookup and updating the README."
    )
    return [
        json.dumps({"type": "system", "session_id": session_id}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": plan}]}}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "orders.py"}}
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "orders.py"}}
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": diff,
                "total_cost_usd": cost_usd,
                "usage": {
                    "input_tokens": 4200,
                    "output_tokens": 880,
                    "cache_read_input_tokens": 1000,
                },
            }
        ),
    ]


def _files_and_counts(diff: str) -> tuple[tuple[str, ...], int, int]:
    """Parse a unified diff for the files it touches and its +/- line counts."""
    files: list[str] = []
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[len("+++ b/") :])
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return tuple(files), added, removed


def _summarize(events: list[ExecEvent], *, session_id: str | None) -> CandidateDiff:
    """Project the normalized event stream into the reviewable :class:`CandidateDiff`.

    ``session_id`` is the interpreter-captured vendor session (the resume token seam), read off the
    executor after the run — it is not itself an ``ExecEvent``.
    """
    result = next((e for e in reversed(events) if isinstance(e, ExecResult)), None)
    if result is None:  # pragma: no cover - the adapter always terminates with a result
        raise RuntimeError("harness run produced no exec.result")
    diff = str(result.output or "")
    usage = next(
        (e for e in reversed(events) if isinstance(e, ExecUsage) and e.cost_usd is not None), None
    )
    tool_calls = tuple(e.tool for e in events if isinstance(e, ExecToolCall))
    files, added, removed = _files_and_counts(diff)
    # Semantic status over exit codes (executor-abstraction §6.1): a diff must be present.
    status = result.status if diff.strip() else "failure"
    manifest = tuple(
        ExecArtifact(artifact_kind="file_change", path=path, mime="text/x-diff") for path in files
    )
    return CandidateDiff(
        diff=diff,
        status=status,
        session_id=session_id,
        cost_usd=usage.cost_usd if usage else None,
        files_changed=files,
        added=added,
        removed=removed,
        tool_calls=tool_calls,
        manifest=manifest,
        events=tuple(events),
    )


async def run_developer_harness(
    *,
    session_id: str = "sess-oms-build",
    revised: bool = False,
    cost_usd: float = 0.42,
) -> CandidateDiff:
    """Run the OMS ``developer`` harness (faked) and return its candidate diff.

    ``revised=False`` emits the flawed first pass (unguarded lookup); ``revised=True`` emits the
    guarded revision. The run flows through the *real* bundled ``claude-code`` adapter — only the
    subprocess is scripted.
    """
    spec = load_adapter_specs()["claude-code"]
    diff = DIFF_REVISED if revised else DIFF_FLAWED
    lines = _developer_jsonl(session_id=session_id, diff=diff, cost_usd=cost_usd, revised=revised)
    harness = _ScriptedHarness(spec, lines)
    task = TaskSpec(
        statement="Implement the approved OMS order-status design; produce a reviewable diff.",
        base_ref="main",
    )
    sandbox = SandboxHandle(root=DEMO_REPO, kind="worktree", network="deny")
    budget = BudgetEnvelope(max_cost_usd=5.0, max_turns=40)
    events = [event async for event in harness.run(task, sandbox, budget)]
    # The vendor session id the interpreter captured (the resume-token seam) — read off the
    # executor after the run, since it is not surfaced as an ExecEvent.
    captured_session = next(iter(harness._sessions.values()), None)
    return _summarize(events, session_id=captured_session)
