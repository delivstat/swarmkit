"""A harness agent gets the context files the task assembled — and they stay out of its diff.

Parity gap 4. `TaskSpec.context_files` was populated with the workspace's `CLAUDE.md` and then
delivered nowhere: assigned in one place, read in none, the same shape as the `mcp_tools` gap closed
in 1.157.0. A harness reads its context from the working tree — that is why `CLAUDE.md` works at all
— so a harness agent ran without the conventions a model agent is handed, with no warning.

Delivery has to respect two things that are easy to get wrong:

**The worktree's own copy wins.** The sandbox is a git worktree at ``base_ref``, so a committed
`CLAUDE.md` is already present and is the project's own. Overwriting it with a copy from elsewhere
would quietly change what the agent is told the rules are.

**A delivered file is not the agent's work.** `collect_diff` runs ``git add --intent-to-add --all``,
so anything the runtime writes into the sandbox appears as authored change — and that diff becomes
the stage's artifact, the next stage's input, and what a human approves at a gate. Letting a
runtime-written file through is the same defect as the display annotation baked into output text
(bug 16), one layer down.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.executors._run import SandboxHandle
from swarmkit_runtime.executors._sandbox import (
    collect_diff,
    deliver_context_files,
    worktree_sandbox,
)


def _git_repo(tmp_path: Path) -> Path:
    """A one-commit repo, so `worktree_sandbox` has a real base ref to detach from."""
    import subprocess  # noqa: PLC0415

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    return repo


def _handle(root: Path) -> SandboxHandle:
    return SandboxHandle(root=root, kind="worktree")


# ---- delivery ---------------------------------------------------------------------------------


def test_a_context_file_reaches_the_working_tree(tmp_path: Path) -> None:
    """The bug: the field was assembled and nothing ever wrote it out."""
    written = deliver_context_files(_handle(tmp_path), {"CLAUDE.md": "# House rules\n"})

    assert written == ("CLAUDE.md",)
    assert (tmp_path / "CLAUDE.md").read_text() == "# House rules\n"


def test_the_worktrees_own_copy_is_never_overwritten(tmp_path: Path) -> None:
    """A committed CLAUDE.md is the project's own. Replacing it with a copy from elsewhere would
    quietly change what the agent is told to do — and would show up as an agent-made edit."""
    (tmp_path / "CLAUDE.md").write_text("# the committed rules\n")

    written = deliver_context_files(_handle(tmp_path), {"CLAUDE.md": "# a different copy\n"})

    assert written == ()
    assert (tmp_path / "CLAUDE.md").read_text() == "# the committed rules\n"


def test_a_nested_path_is_created(tmp_path: Path) -> None:
    written = deliver_context_files(_handle(tmp_path), {"docs/conventions.md": "x"})

    assert written == ("docs/conventions.md",)
    assert (tmp_path / "docs" / "conventions.md").read_text() == "x"


def test_nothing_is_written_when_there_is_nothing_to_deliver(tmp_path: Path) -> None:
    assert deliver_context_files(_handle(tmp_path), {}) == ()


# ---- nothing escapes the sandbox --------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../escaped.md", "../../etc/profile", "a/../../escaped.md"],
    ids=["parent", "deep-parent", "traversal-mid-path"],
)
def test_a_path_that_climbs_out_is_refused(name: str, tmp_path: Path) -> None:
    """Context delivery is not a file-write primitive. A name is refused rather than resolved,
    because resolving it would let a topology write anywhere the runtime can reach."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    written = deliver_context_files(_handle(sandbox), {name: "payload"})

    assert written == ()
    assert not (tmp_path / "escaped.md").exists()


def test_an_absolute_path_is_refused(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = tmp_path / "absolute.md"

    written = deliver_context_files(_handle(sandbox), {str(target): "payload"})

    assert written == ()
    assert not target.exists()


# ---- the diff stays the agent's own -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_delivered_file_is_not_in_the_diff(tmp_path: Path) -> None:
    """The property that matters. `collect_diff` uses `git add --intent-to-add --all`, so a
    runtime-written file would otherwise be presented as authored work — flowing on as the stage's
    artifact and into a human's approval."""
    async with worktree_sandbox(_git_repo(tmp_path), "HEAD") as sandbox:
        delivered = deliver_context_files(sandbox, {"CLAUDE.md": "# rules\n"})
        # What the agent actually did.
        (Path(sandbox.root) / "answer.txt").write_text("the agent's work\n")

        diff = await collect_diff(sandbox, delivered)

    assert "answer.txt" in diff, "the agent's own change must still appear"
    assert "CLAUDE.md" not in diff, "a delivered context file is not the agent's work"


@pytest.mark.asyncio
async def test_the_diff_is_unchanged_when_nothing_was_delivered(tmp_path: Path) -> None:
    """The exclusion must not alter the normal path — an empty exclude list is not a pathspec."""
    async with worktree_sandbox(_git_repo(tmp_path), "HEAD") as sandbox:
        (Path(sandbox.root) / "answer.txt").write_text("work\n")

        diff = await collect_diff(sandbox)

    assert "answer.txt" in diff


# ---- the field is wired ------------------------------------------------------------------------


def test_the_node_delivers_and_excludes() -> None:
    """Stated against the source, because the failure mode is exactly a field that is assembled and
    then quietly not used — which is what this gap was for four milestones."""
    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "deliver_context_files(sandbox, task.context_files)" in src
    # The PROPERTY, not the exact call: `delivered` must reach the exclusion list. It is now
    # joined by the runtime's own sandbox writes (the MCP gateway config), which were being
    # presented as the agent's authored work.
    assert "collect_diff(sandbox, [*delivered," in src, (
        "the delivered files must be excluded from the agent's diff"
    )
