"""Worktree sandbox: provision, diff, teardown (executor-abstraction §6.1, P2 PR3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from swarmkit_runtime.executors import SandboxError, collect_diff, worktree_sandbox


def _init_repo(root: Path) -> None:
    """Create a git repo at ``root`` with one committed file."""

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init")
    git("config", "user.email", "t@t.test")
    git("config", "user.name", "t")
    (root / "seed.txt").write_text("base\n")
    git("add", "-A")
    git("commit", "-m", "seed")


def _worktree_paths(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=repo, check=True, capture_output=True
    ).stdout.decode()
    return {line[len("worktree ") :] for line in out.splitlines() if line.startswith("worktree ")}


@pytest.mark.asyncio
async def test_provision_yields_isolated_checkout_then_tears_down(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    leaked_root: Path | None = None
    async with worktree_sandbox(repo) as handle:
        leaked_root = handle.root
        assert handle.kind == "worktree"
        assert handle.network == "deny"
        # isolated checkout of the base ref — the seed file is present, in its own dir.
        assert handle.root.is_dir()
        assert handle.root.resolve() != repo.resolve()
        assert (handle.root / "seed.txt").read_text() == "base\n"
        assert str(handle.root) in _worktree_paths(repo)

    # teardown ran: the worktree dir is gone and git no longer tracks it.
    assert leaked_root is not None and not leaked_root.exists()
    assert str(leaked_root) not in _worktree_paths(repo)


@pytest.mark.asyncio
async def test_collect_diff_captures_new_and_modified_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    async with worktree_sandbox(repo) as handle:
        (handle.root / "seed.txt").write_text("changed\n")  # modify tracked
        (handle.root / "new.txt").write_text("hello\n")  # untracked (intent-to-add surfaces it)
        diff = await collect_diff(handle)

    assert "seed.txt" in diff
    assert "changed" in diff
    assert "new.txt" in diff
    assert "hello" in diff


@pytest.mark.asyncio
async def test_teardown_runs_even_when_body_raises(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    leaked_root: Path | None = None
    with pytest.raises(RuntimeError, match="boom"):
        async with worktree_sandbox(repo) as handle:
            leaked_root = handle.root
            raise RuntimeError("boom")

    assert leaked_root is not None and not leaked_root.exists()
    assert str(leaked_root) not in _worktree_paths(repo)


@pytest.mark.asyncio
async def test_non_git_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="not a git repository"):
        async with worktree_sandbox(tmp_path):
            pass


def _apply_on_clean_checkout(
    repo: Path, diff: str, dest: Path
) -> subprocess.CompletedProcess[bytes]:
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(dest), "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "apply", "-"], cwd=dest, input=diff.encode(), capture_output=True, check=False
    )


@pytest.mark.asyncio
async def test_a_diff_with_binary_files_applies_on_a_clean_checkout(tmp_path: Path) -> None:
    """#1116: a binary file was a content-less stub, and `git apply` refused the whole patch, so
    the text files beside it were lost too. The diff is the node's output artifact: it has to
    reproduce the sandbox's changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    template = b"PK\x03\x04\x00\xff\x00\x01binary-template\x00\xfe"
    (repo / "template.xlsx").write_bytes(template)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "tpl"], cwd=repo, check=True, capture_output=True)

    changed = template + b"\x00edited\xff"
    async with worktree_sandbox(repo) as handle:
        (handle.root / "copy.xlsx").write_bytes(template)  # a new binary file
        (handle.root / "template.xlsx").write_bytes(changed)  # a modified one
        (handle.root / "a.txt").write_text("hi\n")  # and text beside them
        diff = await collect_diff(handle)

    assert "Binary files" not in diff
    assert "GIT binary patch" in diff
    applied = _apply_on_clean_checkout(repo, diff, tmp_path / "check")
    assert applied.returncode == 0, applied.stderr.decode()
    check = tmp_path / "check"
    assert (check / "a.txt").read_text() == "hi\n"
    assert (check / "copy.xlsx").read_bytes() == template
    assert (check / "template.xlsx").read_bytes() == changed


@pytest.mark.asyncio
async def test_a_text_only_diff_is_unchanged_by_binary_mode(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    async with worktree_sandbox(repo) as handle:
        (handle.root / "seed.txt").write_text("base\nmore\n")
        diff = await collect_diff(handle)
    assert "+more" in diff and "GIT binary patch" not in diff
