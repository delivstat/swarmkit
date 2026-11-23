"""A harness agent receives its archetype's `prompt.system` (#1098).

    just demo-harness-system-prompt

1. A real `WorkspaceRuntime.run` of a one-node harness topology on a fake `claude` that records the
   arguments it was launched with. The archetype's `prompt.system` arrives as
   `--append-system-prompt`, and `executor.started` records how it was delivered.
2. The first-launch command each other bundled adapter would run: the same instructions, ahead of
   the task statement under `## Instructions`, because those adapters have no system-prompt flag.

Before this fix, step 1 showed no `--append-system-prompt` and step 2 showed the statement alone:
the instructions were never read. No network and no API key; needs git and bash.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.executors import (
    BudgetEnvelope,
    DeclarativeExecutor,
    SandboxHandle,
    TaskSpec,
    load_adapter_specs,
    system_prompt_delivery,
)

SYSTEM = "You implement exactly the change described, with tests. Call get_convention first."
STATEMENT = "Add a retry to the order client."

_FAKE_CLAUDE = """#!/usr/bin/env bash
for a in "$@"; do printf '%s\\n<<ARG>>\\n' "$a"; done > "$ARGV_LOG"
echo '{"type":"system","session_id":"s"}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}'
echo '{"type":"result","subtype":"success","result":"done","total_cost_usd":0.0}'
"""

_FILES = {
    "workspace.yaml": (
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: sp-demo, name: sp-demo}\n"
        "governance: {provider: mock}\nmemory: {enabled: false}\n"
    ),
    "archetypes/developer.yaml": (
        "apiVersion: swarmkit/v1\nkind: Archetype\n"
        "metadata: {id: developer, name: Developer, description: A harness node.}\n"
        "role: root\nexecutor: {kind: harness, ref: claude-code}\n"
        f"defaults:\n  prompt: {{system: '{SYSTEM}'}}\n"
        "provenance: {authored_by: human, version: 1.0.0}\n"
    ),
    "topologies/change.yaml": (
        "apiVersion: swarmkit/v1\nkind: Topology\nmetadata: {name: change, version: 0.1.0}\n"
        "agents:\n  root: {id: dev, role: root, archetype: developer}\n"
    ),
}


async def _claude_run(root: Path) -> None:
    ws = root / "ws"
    for rel, text in _FILES.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text)
    ident = ["-c", "user.email=d@d", "-c", "user.name=d"]
    for args in (["init", "-q"], [*ident, "add", "."], [*ident, "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=ws, check=True, capture_output=True)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(_FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    log = root / "argv.log"
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    os.environ["ARGV_LOG"] = str(log)
    os.environ["SWARMKIT_PROVIDER"] = "mock"
    os.environ.pop("ANTHROPIC_API_KEY", None)

    result = await WorkspaceRuntime.from_workspace_path(ws).run("change", STATEMENT)
    argv = [a.strip("\n") for a in log.read_text().split("<<ARG>>\n") if a]
    print("1. claude-code, a real run: what the harness was launched with")
    i = argv.index("--append-system-prompt")
    print(f"   -p                     {argv[argv.index('-p') + 1]!r}")
    print(f"   --append-system-prompt {argv[i + 1]!r}")
    started = [e for e in result.events if getattr(e, "event_type", "") == "executor.started"]
    print(f"   executor.started       {started[0].payload if started else '(none)'}")


def _other_adapters() -> None:
    print("\n2. adapters with no system-prompt flag: the first message they are given")
    specs = load_adapter_specs()
    task = TaskSpec(statement=STATEMENT, system_prompt=SYSTEM)
    for kind in ("codex", "copilot", "gemini-cli", "opencode"):
        argv, _ = DeclarativeExecutor(specs[kind])._launch(
            task, SandboxHandle(root=Path("/tmp/sbx")), BudgetEnvelope(), None, (), None
        )
        first = next(a for a in argv if "## Instructions" in a)
        print(f"   {kind:<11} ({system_prompt_delivery(specs[kind])}) {first!r}")


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="harness-system-prompt-"))
    asyncio.run(_claude_run(root))
    _other_adapters()


if __name__ == "__main__":
    main()
