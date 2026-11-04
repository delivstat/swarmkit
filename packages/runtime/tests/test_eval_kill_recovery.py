"""An eval killed with SIGKILL part-way through is swept and resumed (eval-on-serve.md §4).

Modelled on `test_kill9_recovery.py`. A real `swarmkit eval` subprocess runs a four-case set
against a one-node harness topology. The harness is a fake `claude` on PATH that logs every prompt
it is given and, on the third case, sleeps; the test SIGKILLs the process while it sleeps. Then:

- the row says `running` (nothing could close it), with cases 1-2 recorded;
- once its heartbeat is stale, the sweep any later `swarmkit eval*` command runs marks it
  `interrupted`;
- `eval-runs resume` runs only cases 3 and 4. Cases 1 and 2 ran exactly once; case 3 ran in the
  killed attempt and again on resume (it never got a result); case 4 once.

No network, no key.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_FAKE_CLAUDE = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    # A stand-in for `claude -p ... --output-format stream-json` that logs the prompt it was given.
    printf '%s\\n' "$*" | tr '\\n' ' ' >> "$FAKE_CLAUDE_LOG"; echo >> "$FAKE_CLAUDE_LOG"
    echo '{"type":"system","session_id":"fake-session"}'
    case "$*" in
      *case-three*) touch "$FAKE_CLAUDE_STARTED"; sleep "${FAKE_CLAUDE_SLEEP:-0}" ;;
    esac
    echo '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}'
    echo '{"type":"result","subtype":"success","result":"done","total_cost_usd":0.0}'
    """
)

_FILES = {
    "workspace.yaml": """\
        apiVersion: swarmkit/v1
        kind: Workspace
        metadata: {id: evalkill, name: Eval kill}
        governance: {provider: mock}
        memory: {enabled: false}
        """,
    "archetypes/developer.yaml": """\
        apiVersion: swarmkit/v1
        kind: Archetype
        metadata: {id: developer, name: Developer, description: A harness node.}
        role: root
        executor: {kind: harness, ref: claude-code}
        defaults:
          prompt: {system: You implement.}
        provenance: {authored_by: human, version: 1.0.0}
        """,
    "topologies/change.yaml": """\
        apiVersion: swarmkit/v1
        kind: Topology
        metadata: {name: change, version: 0.1.0}
        agents:
          root: {id: dev, role: root, archetype: developer}
        """,
    "evals/four.yaml": """\
        apiVersion: swarmkit/v1
        kind: EvalSet
        metadata: {id: four}
        target: change
        cases:
          - {id: one, input: do case-one}
          - {id: two, input: do case-two}
          - {id: three, input: do case-three}
          - {id: four, input: do case-four}
        """,
}


def _workspace(root: Path) -> Path:
    ws = root / "ws"
    for rel, text in _FILES.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    ident = ["-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws, check=True)
    subprocess.run(["git", *ident, "add", "."], cwd=ws, check=True)
    subprocess.run(["git", *ident, "commit", "-qm", "init"], cwd=ws, check=True)
    return ws


def _env(bin_dir: Path, log: Path, started: Path, sleep: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_CLAUDE_LOG"] = str(log)
    env["FAKE_CLAUDE_STARTED"] = str(started)
    env["FAKE_CLAUDE_SLEEP"] = sleep
    env["SWARMKIT_PROVIDER"] = "mock"
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _swarmkit(*args: str) -> list[str]:
    return [sys.executable, "-c", "from swarmkit_runtime.cli import app; app()", *args]


def _runs(ws: Path, env: dict[str, str]) -> list[dict[str, object]]:
    out = subprocess.run(
        _swarmkit("eval-runs", "list", str(ws), "--json"),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    runs: list[dict[str, object]] = json.loads(out.stdout)
    return runs


def _ran(log: Path) -> list[str]:
    lines = log.read_text().splitlines() if log.exists() else []
    order = ["case-one", "case-two", "case-three", "case-four"]
    return [next(c for c in order if c in line) for line in lines if any(c in line for c in order)]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_an_eval_killed_mid_case_is_swept_and_resumed_from_its_recorded_cases(
    tmp_path: Path,
) -> None:
    ws = _workspace(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE)
    fake.chmod(0o755)
    log, started = tmp_path / "prompts.log", tmp_path / "started"

    proc = subprocess.Popen(
        _swarmkit("eval", str(ws), "four"),
        env=_env(bin_dir, log, started, "60"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 90
    while not started.exists():
        if proc.poll() is not None:
            out, err = proc.communicate()
            pytest.fail(f"eval ended before case three started:\n{out[-2000:]}\n{err[-2000:]}")
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail("case three never started")
        time.sleep(0.2)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)
    assert proc.returncode == -signal.SIGKILL

    env = _env(bin_dir, log, started, "0")
    [killed] = _runs(ws, env)
    assert killed["status"] == "running", "a SIGKILLed process can't close its own row"
    assert killed["recorded"] == 2
    assert _ran(log) == ["case-one", "case-two", "case-three"]

    # Time passes: the heartbeat goes stale, so the next command's sweep takes the row.
    from sqlalchemy import update  # noqa: PLC0415
    from swarmkit_runtime.persistence import SqliteStore  # noqa: PLC0415
    from swarmkit_runtime.persistence._tables import eval_runs  # noqa: PLC0415

    store = SqliteStore(ws)
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with store.engine.begin() as conn:
        conn.execute(update(eval_runs).values(heartbeat_at=stale))
    [swept] = _runs(ws, env)
    assert swept["status"] == "interrupted"

    resumed = subprocess.run(
        _swarmkit("eval-runs", "resume", str(ws), str(killed["id"])),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stdout[-2000:] + resumed.stderr[-2000:]

    [final] = _runs(ws, env)
    assert final["id"] == killed["id"], "resume continues the same row"
    assert final["status"] == "succeeded"
    assert (final["passed"], final["total"], final["recorded"]) == (4, 4, 4)
    # 1 and 2 once; 3 in the killed attempt and again (it never got a result); 4 once.
    assert _ran(log) == ["case-one", "case-two", "case-three", "case-three", "case-four"]
