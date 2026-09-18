"""A run killed with SIGKILL mid-harness resumes from its checkpoint (failure-path-evaluation.md).

Known to work in production (a WMS incidents application relies on it); this is the regression
test it never had. A three-node chain — mock model → mock model → harness — runs as a real
``swarmkit run`` subprocess. The harness is a fake ``claude`` on PATH that announces a session,
then sleeps; the test SIGKILLs the whole process while it sleeps, then runs ``--resume`` with the
fake set to finish immediately, and reads the audit back:

- CHECKPOINT RECOVERY WORKS: the resumed run finishes the chain from where it was killed. The
  node the kill interrupted (`build`, the harness) re-runs; the node already checkpointed
  (`prepare`) does not. This is the behaviour a WMS incidents app relies on.
- THE AUDIT OF THE KILLED ATTEMPT SURVIVES: the write-through journal (audit-event-journal.md)
  persists each event when it happens, so right after the SIGKILL the store already holds the
  trail up to the kill — including the harness's `executor.started`. (Before 1.239.0 this was
  empty: events buffered in memory and flushed only at the run boundary a hard kill never reaches.)

No network, no key: the model nodes use the mock provider and the fake harness speaks Claude
Code's stream-json in three lines.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

_FAKE_CLAUDE = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    # A stand-in for `claude -p ... --output-format stream-json`: session line, (sleep), result.
    echo '{"type":"system","session_id":"fake-session-1"}'
    touch "$FAKE_CLAUDE_STARTED"
    sleep "${FAKE_CLAUDE_SLEEP:-0}"
    echo '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}'
    echo '{"type":"result","subtype":"success","result":"implemented","total_cost_usd":0.0}'
    """
)

_WORKSPACE = textwrap.dedent(
    """\
    apiVersion: swarmkit/v1
    kind: Workspace
    metadata: {id: kill9, name: Kill9}
    governance: {provider: mock}
    memory: {enabled: false}
    """
)

_ARCHETYPES = {
    "planner": textwrap.dedent(
        """\
        apiVersion: swarmkit/v1
        kind: Archetype
        metadata: {id: planner, name: Planner, description: A mock model node.}
        role: worker
        defaults:
          model: {provider: mock, name: mock}
          prompt: {system: You plan.}
        provenance: {authored_by: human, version: 1.0.0}
        """
    ),
    "developer": textwrap.dedent(
        """\
        apiVersion: swarmkit/v1
        kind: Archetype
        metadata: {id: developer, name: Developer, description: A harness node.}
        role: worker
        executor: {kind: harness, ref: claude-code}
        defaults:
          prompt: {system: You implement.}
        provenance: {authored_by: human, version: 1.0.0}
        """
    ),
}

_TOPOLOGY = textwrap.dedent(
    """\
    apiVersion: swarmkit/v1
    kind: Topology
    metadata: {name: change, version: 0.1.0}
    # lead -> prepare -> build: three graph nodes, so the checkpoint written when `lead` handed
    # off to `prepare` is what a resume picks up. (A `depends_on` DAG runs its children INSIDE the
    # root's node and would checkpoint nothing until all of them finished.)
    agents:
      root:
        id: lead
        role: root
        archetype: planner
        model: {provider: mock, name: mock}
        children:
          - id: prepare
            role: worker
            archetype: planner
            children:
              - id: build
                role: worker
                archetype: developer
    """
)


def _workspace(root: Path) -> Path:
    ws = root / "ws"
    (ws / "archetypes").mkdir(parents=True)
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(_WORKSPACE)
    for name, text in _ARCHETYPES.items():
        (ws / "archetypes" / f"{name}.yaml").write_text(text)
    (ws / "topologies" / "change.yaml").write_text(_TOPOLOGY)
    # The worktree sandbox needs a git repository to branch from.
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws, check=True)
    subprocess.run(["git", *ident, "add", "."], cwd=ws, check=True)
    subprocess.run(["git", *ident, "commit", "-qm", "init"], cwd=ws, check=True)
    return ws


def _env(bin_dir: Path, started: Path, sleep: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_CLAUDE_STARTED"] = str(started)
    env["FAKE_CLAUDE_SLEEP"] = sleep
    env["SWARMKIT_PROVIDER"] = "mock"
    env["SWARMKIT_MOCK_DELEGATE"] = "1"  # the root delegates to its children (see _mock.py)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _audit(ws: Path) -> list[tuple[str, str]]:
    """Every (event_type, agent_id) in the workspace's audit store, oldest first."""
    import asyncio  # noqa: PLC0415

    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415
    from swarmkit_runtime.persistence._service import reset_storage_cache  # noqa: PLC0415

    async def _read() -> list[tuple[str, str]]:
        reset_storage_cache()
        provider = storage_for_workspace(ws).audit_provider()
        rows = [(e.event_type, e.agent_id or "") async for e in provider.query(limit=10_000)]
        return list(reversed(rows)) if rows and rows[0][0].startswith("run.") is False else rows

    return asyncio.run(_read())


def _swarmkit(*args: str) -> list[str]:
    return [sys.executable, "-c", "from swarmkit_runtime.cli import app; app()", *args]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_run_killed_mid_harness_resumes_from_its_checkpoint(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE)
    fake.chmod(0o755)
    started = tmp_path / "harness-started"

    # Attempt 1: the harness sleeps long enough for us to kill the run underneath it.
    proc = subprocess.Popen(
        _swarmkit("run", str(ws), "change", "--input", "Implement it."),
        env=_env(bin_dir, started, "60"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 60
    while not started.exists():
        if proc.poll() is not None:
            _, err = proc.communicate()
            pytest.fail(f"run ended before the harness started:\n{err[-2000:]}")
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail("the harness never started")
        time.sleep(0.2)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)
    assert proc.returncode == -signal.SIGKILL

    # The write-through journal makes the pre-kill trail durable: the events up to and including
    # the harness starting are in the store, though the process was killed with -9 and never
    # reached its end-of-run flush.
    after_kill = _audit(ws)
    assert ("agent.completed", "lead") in after_kill
    assert ("agent.completed", "prepare") in after_kill
    assert ("executor.started", "build") in after_kill, "the harness start is durable"
    assert ("executor.result", "build") not in after_kill, "the harness had not finished"
    assert (ws / ".swarmkit" / "state" / "checkpoints.db").exists(), "the checkpoint is durable too"

    # Attempt 2: resume. The fake harness now finishes at once.
    started.unlink()
    resumed = subprocess.run(
        _swarmkit("run", str(ws), "change", "--resume"),
        env=_env(bin_dir, started, "0"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr[-3000:]

    final = _audit(ws)
    # Append-only: the pre-kill trail is still there, and the resume added to it without erasing.
    assert final[: len(after_kill)] == after_kill
    # The killed attempt's harness start plus the resumed one — both on the record.
    assert final.count(("executor.started", "build")) == 2, "both attempts are on the record"
    # Only the resumed attempt produced a result and completed the chain (checkpoint recovery).
    assert final.count(("executor.result", "build")) == 1
    assert ("agent.completed", "lead") in final, "the run finished after the resume"
