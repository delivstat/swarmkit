"""GitHub Copilot CLI as a SwarmKit harness — the bundled `copilot` adapter (#1091, #1090).

    uv run python packages/runtime/demos/copilot_harness.py          # deterministic, no network
    uv run python packages/runtime/demos/copilot_harness.py --live   # real `copilot` on PATH

Deterministic mode drives the REAL bundled adapter over a stream recorded from Copilot CLI 1.0.103
(only the subprocess is scripted; it also writes the usage report Copilot writes at exit). It shows
three things:

1. What the adapter translates: one message per model turn, each tool call and its outcome.
2. Where the tokens come from: Copilot's JSONL has none; the `--usage-output-file` report does.
   The same run with the report ignored is the 0-tokens, $0.00 record #1090 fixed.
3. Why `max_turns` matters: a denied tool does not end a Copilot session, the model retries it.

Live mode runs `copilot` for real on a one-line bug in a throwaway git repo, BYOK through
OpenRouter: set COPILOT_PROVIDER_API_KEY (an OpenRouter key) and, optionally, COPILOT_DEMO_MODEL
(default `openai/gpt-4.1-mini`). It costs a few cents.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from swarmkit_runtime.executors import (
    BudgetEnvelope,
    DeclarativeExecutor,
    ExecMessage,
    ExecResult,
    ExecToolCall,
    ExecUsage,
    SandboxHandle,
    TaskSpec,
    collect_diff,
    enforce_budget,
    load_adapter_specs,
    worktree_sandbox,
)
from swarmkit_runtime.model_providers._pricing import estimate_cost

FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/harness-streams"
STREAM = (FIXTURES / "copilot.jsonl").read_text().splitlines()
REPORT = (FIXTURES / "copilot-usage.json").read_text()


class _Recorded(DeclarativeExecutor):
    """The bundled adapter with the subprocess replaced by the recording."""

    def __init__(self, spec: Any, lines: list[str], report: str | None, **kw: Any) -> None:
        super().__init__(spec, **kw)
        self._lines, self._report = lines, report

    async def _open_stream(
        self, argv: list[str], env: Mapping[str, str], cwd: Path, run_id: str
    ) -> AsyncIterator[str]:
        for line in self._lines:
            yield line
        if self._report is not None:
            (cwd / argv[argv.index("--usage-output-file") + 1]).write_text(self._report)


async def _collect(ex: DeclarativeExecutor, root: Path, budget: BudgetEnvelope) -> list[Any]:
    stream = ex.run(TaskSpec(statement="Fix the bug in calc.py"), SandboxHandle(root=root), budget)
    return [e async for e in enforce_budget(stream, budget)]


def _show(events: list[Any], model: str) -> None:
    turns = 0
    for e in events:
        if isinstance(e, ExecMessage):
            turns += 1
            print(f"    turn {turns}: message {e.text!r}")
        elif isinstance(e, ExecToolCall):
            mark = " ✗" if e.status == "error" else ""
            print(f"            tool  {e.tool}{mark}")
        elif isinstance(e, ExecResult):
            print(f"    result: {e.status}")
    tin = sum(e.input_tokens for e in events if isinstance(e, ExecUsage))
    tout = sum(e.output_tokens for e in events if isinstance(e, ExecUsage))
    cost = estimate_cost(model, tin, tout)
    print(f"    tokens: {tin:,} in / {tout:,} out · cost ${cost:.4f} ({model})")


async def deterministic() -> None:
    spec = load_adapter_specs(None)["copilot"]
    priced = "openai/gpt-4.1-mini"  # the recording ran kimi-k2.5, which the price table lacks
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        print("1. A recorded Copilot run through the bundled adapter (usage from the exit report)")
        _show(await _collect(_Recorded(spec, STREAM, REPORT), root, BudgetEnvelope()), priced)

        print("\n2. The same run if the report were not read — what #1090 fixed")
        no_report = replace(spec, report=None)
        _show(await _collect(_Recorded(no_report, STREAM, None), root, BudgetEnvelope()), priced)

        print("\n3. A denied tool, retried every turn — stopped by max_turns=5")
        denied_turn = [line for line in STREAM if '"type":"assistant.message"' in line][1]
        events = await _collect(
            _Recorded(spec, [denied_turn] * 50, None), root, BudgetEnvelope(max_turns=5)
        )
        last = events[-1]
        assert isinstance(last, ExecResult)
        print(
            f"    {sum(isinstance(e, ExecMessage) for e in events)} turns, then result: "
            f"{last.status} ({last.exit_metadata.get('reason')})"
        )


async def live() -> None:
    if shutil.which("copilot") is None:
        sys.exit("`copilot` is not on PATH — `npm install -g @github/copilot`")
    if not os.environ.get("COPILOT_PROVIDER_API_KEY"):
        sys.exit("set COPILOT_PROVIDER_API_KEY to an OpenRouter key for the BYOK live run")
    model = os.environ.get("COPILOT_DEMO_MODEL", "openai/gpt-4.1-mini")
    spec = load_adapter_specs(None)["copilot"]
    ex = DeclarativeExecutor(
        spec,
        config={
            "auth_mode": "api_key",
            "provider_base_url": "https://openrouter.ai/api/v1",
            "model": model,
            "allowed_tools": "view,edit",
        },
    )
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        git = ["git", "-c", "user.email=demo@example.com", "-c", "user.name=demo"]
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run([*git, "add", "."], cwd=repo, check=True)
        subprocess.run([*git, "commit", "-qm", "init"], cwd=repo, check=True)
        async with worktree_sandbox(repo, "HEAD") as sandbox:
            budget = BudgetEnvelope(max_turns=15, max_wall_clock_minutes=5)
            task = TaskSpec(statement="calc.py: add() returns the difference. Fix it.")
            events = [e async for e in enforce_budget(ex.run(task, sandbox, budget), budget)]
            print(f"Live Copilot run ({model}, BYOK via OpenRouter)")
            _show(events, model)
            print("\n    diff:")
            print("    " + (await collect_diff(sandbox, [])).replace("\n", "\n    "))


if __name__ == "__main__":
    asyncio.run(live() if "--live" in sys.argv else deterministic())
