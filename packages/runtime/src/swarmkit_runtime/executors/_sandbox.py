"""Worktree sandbox for harness executors (executor-abstraction §6.1, P2 PR3).

A harness executor runs an external agentic subprocess that edits files. It does so **inside a
provisioned sandbox**, never the live workspace. In P2 the sandbox is a **git worktree** detached at
a base ref: the harness gets an isolated checkout, and its output is a *diff* — the ownership rule
(§6.1) is that the executor node **produces** a diff and never **integrates** it, so this module
exposes provisioning, diff collection, and teardown, but deliberately **no** commit-back / merge /
push path.

``network`` is ``deny`` on the handle. In P2 this is advisory (the runner grants no network tools);
the enforcing egress proxy + container sandbox are their own hard piece, deferred to P3+.

Provisioning and teardown are core's job, not the adapter's — the adapter only sees a
:class:`~swarmkit_runtime.executors._run.SandboxHandle`. Teardown runs on success *and* failure.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from swarmkit_runtime.executors._run import SandboxHandle


class SandboxError(RuntimeError):
    """A git worktree could not be provisioned, inspected, or torn down."""


async def _git(*args: str, cwd: Path) -> tuple[int, str, str]:
    """Run ``git <args>`` in ``cwd``; return ``(returncode, stdout, stderr)``."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


@asynccontextmanager
async def worktree_sandbox(
    repo_root: Path | str,
    base_ref: str = "HEAD",
    *,
    prefix: str = "swarmkit-exec-",
) -> AsyncIterator[SandboxHandle]:
    """Provision a detached git worktree at ``base_ref`` and tear it down on exit.

    The executor runs inside the yielded handle's ``root``. The worktree is removed on both the
    success and failure paths; a failed ``git worktree remove`` falls back to a force-delete + prune
    so a partial checkout never leaks. Raises :class:`SandboxError` if ``repo_root`` is not a git
    repository or the worktree cannot be created.
    """
    repo = Path(repo_root).resolve()
    code, _, err = await _git("rev-parse", "--git-dir", cwd=repo)
    if code != 0:
        raise SandboxError(f"{repo} is not a git repository: {err.strip()}")

    base = Path(tempfile.mkdtemp(prefix=prefix))
    work_path = base / "worktree"
    code, _, err = await _git("worktree", "add", "--detach", str(work_path), base_ref, cwd=repo)
    if code != 0:
        shutil.rmtree(base, ignore_errors=True)
        raise SandboxError(
            f"failed to provision worktree at {work_path} from {base_ref!r}: {err.strip()}"
        )

    handle = SandboxHandle(root=work_path, kind="worktree", network="deny")
    try:
        yield handle
    finally:
        code, _, _ = await _git("worktree", "remove", "--force", str(work_path), cwd=repo)
        if code != 0:
            shutil.rmtree(work_path, ignore_errors=True)
            await _git("worktree", "prune", cwd=repo)
        shutil.rmtree(base, ignore_errors=True)


async def collect_diff(handle: SandboxHandle) -> str:
    """Return the harness's changes as a unified diff against the sandbox's base ref.

    Uses ``git add --intent-to-add`` so newly created (untracked) files appear as additions without
    staging their content — the produced diff is the node's output artifact (§6.1). This never
    commits, merges, or pushes: integration is a downstream node's decision, gated as usual.
    """
    root = handle.root
    await _git("add", "--intent-to-add", "--all", cwd=root)
    code, out, err = await _git("diff", cwd=root)
    if code != 0:
        raise SandboxError(f"failed to collect diff from {root}: {err.strip()}")
    return out
