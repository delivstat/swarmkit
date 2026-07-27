"""Demo: the `swarmkit memory` CLI over a seeded governed-memory store
(design/details/governed-memory.md — CLI ⇄ serve parity).

Builds a temporary workspace that declares governed memory, seeds a few facts, then runs the real
CLI commands (search, get --history, quarantine, resolve). The CLI and the serve `/memory` endpoints
resolve the store through the SAME service seam (WorkspaceRuntime.governed_memory).
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from swarmkit_runtime.cli import app as cli_app
from swarmkit_runtime.governed_memory import GovernedMemoryStore, MemoryCandidate, ReconcileVerdict
from typer.testing import CliRunner

_runner = CliRunner()
REFERENCE_WS = Path(__file__).resolve().parents[3] / "reference"


async def _contradict(_req: object) -> ReconcileVerdict:
    return ReconcileVerdict(op="contradict", reasoning="reverses a firmly-held preference")


def _make_ws(root: Path) -> None:
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\n"
        "metadata:\n  id: mem-demo\n  name: Mem Demo\n  description: governed-memory CLI demo.\n"
    )
    for skill in ("governed-memory.yaml", "memory-reconcile.yaml"):
        shutil.copy(REFERENCE_WS / "skills" / skill, root / "skills" / skill)


def _run(w: str, *args: str) -> None:
    print(f"\n$ swarmkit memory {' '.join(args)}")
    print(_runner.invoke(cli_app, ["memory", *args, "-w", w]).stdout.rstrip())


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_ws(root)
        store = GovernedMemoryStore.for_workspace(root, reconciler=_contradict)
        store.write(MemoryCandidate(subject="user:alice", attribute="lang", value="Python"))
        store.write(MemoryCandidate(subject="user:alice", attribute="editor", value="neovim"))
        store.write(MemoryCandidate(subject="user:alice", attribute="lang", value="Rust"))  # update
        await store.awrite(MemoryCandidate(subject="user:alice", attribute="lang", value="COBOL"))

        w = str(root)
        _run(w, "search")
        _run(w, "get", "user:alice", "lang", "--history")
        _run(w, "quarantine")
        _run(w, "resolve", "1", "--by", "curator:alice", "--reject")

    print("\n✓ same store, same JSON over `swarmkit memory` and the serve /memory endpoints.")


if __name__ == "__main__":
    asyncio.run(main())
