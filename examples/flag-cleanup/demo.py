"""Feature-flag cleanup on SwarmKit — the runnable demo.

See docs/site/case-studies/feature-flag-cleanup.md.

Phase 2 is the star: a harness agent removes a stale flag in an isolated worktree and produces a
candidate diff, which the `cleanup-review` funnel judges and a human signs off. Two scenarios — a
clean removal, and one where the first diff leaves a wrapper reference behind, the review finds it,
and the critique routes back to the harness for a bounded revision.

Deterministic — no keys, no network, no real harness, no server. The harness seam is real: the
bundled `claude-code` adapter translates a scripted `stream-json` transcript into normalized exec
events through its own event-map; only the subprocess launch is faked (the documented
`DeclarativeExecutor._open_stream` test seam). The gate is the real machinery —
`compile_funnel_gate` with the `code-review` decision skill (scripted verdicts) and
`resolve_multiparty` over a file-backed review queue seeded with the reviewer's sign-off.

    uv run python examples/flag-cleanup/demo.py
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from swarmkit_runtime.executors._declarative import DeclarativeExecutor, load_adapter_specs
from swarmkit_runtime.executors._events import ExecResult, ExecToolCall, ExecUsage
from swarmkit_runtime.executors._run import BudgetEnvelope, SandboxHandle, TaskSpec
from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.governance._approval import ApprovalPolicy, GateStatus, evaluate
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._gate_funnel import (
    build_decision_judge,
    build_multiparty_approver,
    compile_funnel_gate,
)
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.review import FileReviewQueue
from swarmkit_runtime.review._multiparty import collect_resolutions, open_gate, role_task_item_id

WS = Path(__file__).resolve().parent / "workspace"
REPO = Path(__file__).resolve().parent / "fixtures" / "demo-repo"

# The reviewer who signs off (flag-roles.yaml). exclude_author bars the cleaner from approving.
APPROVER = (0, "flag-reviewer", "alice")

# The flag's winning branch (v2, 100% rollout) inlined; the wrapper reference and dead branch gone.
DIFF_CLEAN = """\
diff --git a/checkout.py b/checkout.py
--- a/checkout.py
+++ b/checkout.py
@@ -5,9 +5,5 @@ from flags import is_enabled
 def checkout_total(items: list[float]) -> float:
-    if is_enabled("checkout_v2_enabled"):
-        # v2: tax applied per line (the winning branch — 100% rollout).
-        return sum(round(i * 1.08, 2) for i in items)
-    # v1: legacy flat tax (dead — nobody is on it).
-    return round(sum(items) * 1.08, 2)
+    return sum(round(i * 1.08, 2) for i in items)
diff --git a/flags.py b/flags.py
--- a/flags.py
+++ b/flags.py
@@ -1,6 +1,6 @@
 \"\"\"A tiny dependency-injected flag wrapper.\"\"\"
-_ROLLOUT = {"checkout_v2_enabled": 100}
+_ROLLOUT: dict[str, int] = {}
"""

# The FLAWED first pass: inlines the branch but leaves the now-unused `is_enabled` import and the
# flag key in the wrapper — the review finding that routes back.
DIFF_FLAWED = """\
diff --git a/checkout.py b/checkout.py
--- a/checkout.py
+++ b/checkout.py
@@ -5,9 +5,5 @@ from flags import is_enabled
 def checkout_total(items: list[float]) -> float:
-    if is_enabled("checkout_v2_enabled"):
-        # v2: tax applied per line (the winning branch — 100% rollout).
-        return sum(round(i * 1.08, 2) for i in items)
-    # v1: legacy flat tax (dead — nobody is on it).
-    return round(sum(items) * 1.08, 2)
+    return sum(round(i * 1.08, 2) for i in items)
"""


class _ScriptedHarness(DeclarativeExecutor):
    """A `claude-code` executor whose subprocess is a scripted JSONL stream (real event-map)."""

    def __init__(self, spec: Any, lines: list[str]) -> None:
        super().__init__(spec)
        self._lines = lines

    async def _open_stream(  # type: ignore[override]
        self, argv: list[str], env: Any, cwd: Path, run_id: str
    ) -> Any:
        for line in self._lines:
            yield line if line.endswith("\n") else line + "\n"


def _transcript(*, diff: str, revised: bool) -> list[str]:
    plan = (
        "Revising: removing the leftover flag reference the review found."
        if revised
        else "Removing checkout_v2_enabled: inlining the 100% branch and dropping the wrapper key."
    )
    tools = [("Read", "checkout.py"), ("Edit", "checkout.py"), ("Edit", "flags.py")]
    lines = [
        json.dumps({"type": "system", "session_id": "sess-flag-checkout"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": plan}]}}),
    ]
    for name, path in tools:
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "name": name, "input": {"file_path": path}}
                        ]
                    },
                }
            )
        )
    # The harness runs the build + tests itself (Phase 2's deterministic checks live in this trail).
    lines.append(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}
                    ]
                },
            }
        )
    )
    lines.append(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": diff,
                "total_cost_usd": 0.18 if revised else 0.31,
                "usage": {"input_tokens": 5200, "output_tokens": 640},
            }
        )
    )
    return lines


async def _run_harness(*, revised: bool) -> tuple[str, dict[str, Any]]:
    """Run the cleanup harness (scripted) through the real adapter; return (diff, telemetry)."""
    spec = load_adapter_specs()["claude-code"]
    diff = DIFF_CLEAN if revised else DIFF_FLAWED
    harness = _ScriptedHarness(spec, _transcript(diff=diff, revised=revised))
    task = TaskSpec(statement="Remove the stale flag checkout_v2_enabled.", base_ref="main")
    sandbox = SandboxHandle(root=REPO, kind="worktree", network="deny")
    budget = BudgetEnvelope(max_cost_usd=8.0, max_turns=40, max_wall_clock_minutes=60)
    events = [e async for e in harness.run(task, sandbox, budget)]
    result = next(e for e in reversed(events) if isinstance(e, ExecResult))
    usage = next((e for e in reversed(events) if isinstance(e, ExecUsage)), None)
    tools = [e.tool for e in events if isinstance(e, ExecToolCall)]
    telem = {
        "tools": tools,
        "cost_usd": usage.cost_usd if usage and usage.cost_usd else 0.0,
        "status": result.status,
    }
    return str(result.output or ""), telem


class _Reviewer(MockGovernanceProvider):
    """The code-review decision skill, scripted pass / needs-revision + append-only audit."""

    def __init__(self, verdicts: list[bool]) -> None:
        super().__init__()
        self._verdicts, self._n = verdicts, 0

    async def evaluate_decision_skill(self, **kw: Any) -> DecisionSkillResult:
        ok = self._verdicts[self._n] if self._n < len(self._verdicts) else True
        self._n += 1
        if ok:
            return DecisionSkillResult(
                skill_id=kw.get("skill_id", ""),
                verdict="pass",
                confidence=0.93,
                reasoning="every reference to checkout_v2_enabled is gone; branch inlined",
            )
        return DecisionSkillResult(
            skill_id=kw.get("skill_id", ""),
            verdict="needs-changes",
            confidence=0.4,
            reasoning="flags.py still carries the checkout_v2_enabled key and unused import",
        )


async def _run_gate(*, verdicts: list[bool], clean_first: bool, correlation_id: str) -> str:
    ws = resolve_workspace(WS)
    spec = dict(ws.funnels["cleanup-review"].spec)
    gate_id = f"{correlation_id}:cleaner"
    diffs: list[str] = []

    with tempfile.TemporaryDirectory() as d:
        queue = FileReviewQueue(Path(d))
        policy = ApprovalPolicy.from_dict(spec["approve"])
        open_gate(
            queue, gate_id=gate_id, topology_id=correlation_id, agent_id="cleaner", policy=policy
        )
        idx, role, who = APPROVER
        queue.record_resolution(role_task_item_id(gate_id, idx, role), "approved", who)

        async def drafter(state: Any) -> str:
            is_revision = state.get("critique") is not None
            diff, telem = await _run_harness(revised=is_revision or clean_first)
            diffs.append(diff)
            label = f"draft#{len(diffs)}{', revised' if is_revision else ''}"
            if is_revision:
                print(f'   ↩ route-back — critique to the harness: "{state.get("critique")}"')
            print(
                f"   harness (claude-code, worktree, net=deny) {label}: "
                f"tools={' → '.join(telem['tools'])}  cost=${telem['cost_usd']:.2f}  "
                f"status={telem['status']}"
            )
            return diff

        judge = _narrate(
            build_decision_judge(spec, governance=_Reviewer(verdicts), agent_id="cleaner")
        )
        approver = build_multiparty_approver(
            spec,
            governance=MockGovernanceProvider(),
            review_queue=queue,
            registry=ws.role_registry,
            topology_id=correlation_id,
            agent_id="cleaner",
            gate_id=gate_id,
            author="cleaner",
            max_wait_seconds=1,
        )
        compiled = compile_funnel_gate(spec, drafter=drafter, approver=approver, judge=judge)
        state = await compiled.ainvoke({"artifact": "", "retries": 0})
        collected = collect_resolutions(queue, gate_id=gate_id, policy=policy)
        ev = evaluate(policy, ws.role_registry, collected, "cleaner")

    if ev.status is GateStatus.APPROVED:
        print(f"   ✓ {', '.join(sorted(ev.distinct_approvers))} signed off → open PR")
    return str(state.get("outcome", "rejected"))


def _narrate(judge: Any) -> Any:
    async def narrated(artifact: str) -> Any:
        out = await judge(artifact)
        if out.passed:
            print(f"   code-review → PASS {out.score:.2f}  advance")
        else:
            print(
                f"   code-review → NEEDS-REVISION {out.score:.2f}  ⇒ route back\n"
                f'     finding: "{out.critique}"'
            )
        return out

    return narrated


async def main() -> None:
    print("① Clean removal — harness → diff → code-review passes → reviewer approves")
    out1 = await _run_gate(verdicts=[True], clean_first=True, correlation_id="FLAG-101")
    print(f"   GATE: {out1.upper()}\n")

    print("② Route-back — first diff leaves a leftover reference; the review catches it")
    out2 = await _run_gate(verdicts=[False, True], clean_first=False, correlation_id="FLAG-102")
    print(f"   GATE: {out2.upper()}\n")

    print("✓ flag-cleanup demo complete — no keys, no network, real adapter + gate")


if __name__ == "__main__":
    asyncio.run(main())
