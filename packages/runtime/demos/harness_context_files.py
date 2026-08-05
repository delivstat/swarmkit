#!/usr/bin/env python
"""Demo: a harness agent gets its context files, and they stay out of its diff.

    uv run python packages/runtime/demos/harness_context_files.py

Builds a real git repo, opens the same worktree sandbox a harness node uses, delivers a context
file, writes something as "the agent", and collects the diff.

Before this change `TaskSpec.context_files` was assembled and delivered nowhere, so a harness agent
ran without the workspace conventions a model agent is handed. The interesting half is the second
one: delivery must not put a runtime-written file into the diff, because that diff is the node's
output artifact — the next stage's input, and what a human approves at a gate.
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from swarmkit_runtime.executors._sandbox import (
    collect_diff,
    deliver_context_files,
    worktree_sandbox,
)

CONTEXT = {"CLAUDE.md": "# House rules\n\nAlways cite the table you read.\n"}


def _git_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "d@d"], ["config", "user.name", "demo"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "seed.txt").write_text("a file the repo already had\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    return repo


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = _git_repo(Path(tmp))

        async with worktree_sandbox(repo, "HEAD") as sandbox:
            delivered = deliver_context_files(sandbox, CONTEXT)
            print(f"delivered into the sandbox: {list(delivered)}")
            print(f"  the agent can read it:    {(Path(sandbox.root) / 'CLAUDE.md').is_file()}")

            # Stand in for the harness: the agent does its actual work.
            (Path(sandbox.root) / "answer.md").write_text("# Findings\n\nFrom pgm_hold.\n")

            diff = await collect_diff(sandbox, delivered)

        print("\nthe collected diff — the node's output artifact:\n")
        for line in diff.splitlines():
            if line.startswith(("+++", "---", "diff ")):
                print(f"  {line}")

        print(f"\n  agent's own file in the diff:      {'answer.md' in diff}")
        print(f"  delivered context file in the diff: {'CLAUDE.md' in diff}")
        print("\nThe second must be False: a file the runtime wrote is not the agent's work, and")
        print("this diff is what the next stage reads and what a human approves at a gate.")


if __name__ == "__main__":
    asyncio.run(main())
