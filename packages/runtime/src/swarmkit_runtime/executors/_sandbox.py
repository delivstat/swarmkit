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
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from swarmkit_runtime.executors._run import SandboxHandle

logger = logging.getLogger("swarmkit.sandbox")


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


def deliver_context_files(handle: SandboxHandle, files: Mapping[str, str]) -> tuple[str, ...]:
    """Materialise the task's context files into the sandbox, and report what was written.

    A harness reads its context from the working tree — that is the whole mechanism, and it is why
    `CLAUDE.md` works at all. ``TaskSpec.context_files`` was assembled and then never delivered, so
    a harness agent ran without the conventions a model agent is given.

    Two rules:

    * **An existing file is never overwritten.** The sandbox is a worktree of the repo at
      ``base_ref``, so a committed ``CLAUDE.md`` is already there and is the project's own.
      Replacing it with a copy from elsewhere would quietly change what the agent is told to do.
    * **Nothing escapes the sandbox.** A name containing ``..`` or an absolute path is refused
      rather than resolved — context delivery is not a file-write primitive.

    Returns the paths actually written, which the caller must exclude from the collected diff.
    """
    written: list[str] = []
    root = handle.root.resolve()
    for name, content in (files or {}).items():
        target = (root / name).resolve()
        if not str(target).startswith(str(root) + os.sep):
            logger.warning("refusing to deliver context file outside the sandbox: %s", name)
            continue
        if target.exists():
            continue  # the worktree's own copy wins
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(name)
    return tuple(written)


async def collect_diff(handle: SandboxHandle, exclude: Sequence[str] = ()) -> str:
    """Return the harness's changes as a unified diff against the sandbox's base ref.

    Uses ``git add --intent-to-add`` so newly created (untracked) files appear as additions without
    staging their content — the produced diff is the node's output artifact (§6.1). This never
    commits, merges, or pushes: integration is a downstream node's decision, gated as usual.

    ``exclude`` drops paths the RUNTIME wrote — delivered context files. They are not the agent's
    work, and the diff is the agent's output artifact: it becomes the stage's artifact, the next
    stage's input, and what a human approves at a gate. Letting a runtime-written file through
    would present it as authored work, which is the same defect as annotating the output text.
    """
    root = handle.root
    await _git("add", "--intent-to-add", "--all", cwd=root)
    args = ["diff"]
    if exclude:
        # Pathspec form, so the exclusion is git's own and cannot mangle the diff body.
        args += ["--", ".", *(f":(exclude){path}" for path in exclude)]
    code, out, err = await _git(*args, cwd=root)
    if code != 0:
        raise SandboxError(f"failed to collect diff from {root}: {err.strip()}")
    return out
