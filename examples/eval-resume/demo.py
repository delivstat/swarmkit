"""An eval interrupted part-way through, then resumed from the cases it already scored.

    just demo-eval-resume

Builds a throwaway workspace with a four-case eval set against a one-node harness topology. The
harness is a fake `claude` on PATH: it answers at once, except on the third case, where it hangs
until the demo interrupts the eval with Ctrl-C (SIGINT). Then:

1. `swarmkit eval-runs list` shows the run `interrupted`, two of four cases recorded;
2. `swarmkit eval-runs resume` runs only cases three and four, on the same run;
3. `swarmkit eval-runs show` prints the finished run.

A SIGKILL or a power cut is the same story, except the run stays `running` until its heartbeat
is five minutes stale and the next `swarmkit eval*` command sweeps it
(tests/test_eval_kill_recovery.py).

No network and no API key: the harness is fake and governance is the mock provider. Needs git and
bash.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

_FAKE_CLAUDE = """\
#!/usr/bin/env bash
echo '{"type":"system","session_id":"demo"}'
case "$*" in *case-three*) [ -n "$HANG" ] && { touch "$STARTED"; sleep 600; } ;; esac
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}'
echo '{"type":"result","subtype":"success","result":"done","total_cost_usd":0.0}'
"""

_FILES = {
    "workspace.yaml": """\
        apiVersion: swarmkit/v1
        kind: Workspace
        metadata: {id: eval-resume-demo, name: Eval resume demo}
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
        metadata: {id: four, description: Four cases; the third one hangs the first time.}
        target: change
        cases:
          - {id: one, input: do case-one, expect: {contains: [done]}}
          - {id: two, input: do case-two, expect: {contains: [done]}}
          - {id: three, input: do case-three, expect: {contains: [done]}}
          - {id: four, input: do case-four, expect: {contains: [done]}}
        """,
}


def _swarmkit(*args: str) -> list[str]:
    return [sys.executable, "-c", "from swarmkit_runtime.cli import app; app()", *args]


def _show(cmd: list[str], env: dict[str, str]) -> str:
    print(f"\n$ swarmkit {' '.join(cmd)}")
    out = subprocess.run(_swarmkit(*cmd), env=env, capture_output=True, text=True, check=False)
    print(textwrap.indent(out.stdout.rstrip(), "  "))
    return out.stdout


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="eval-resume-demo-"))
    ws = root / "ws"
    for rel, text in _FILES.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(textwrap.dedent(text))
    ident = ["-c", "user.email=demo@example.com", "-c", "user.name=demo"]
    for git in (["init", "-q", "-b", "main"], [*ident, "add", "."], [*ident, "commit", "-qm", "x"]):
        subprocess.run(["git", *git], cwd=ws, check=True)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(_FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    started = root / "started"

    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    env.update({"SWARMKIT_PROVIDER": "mock", "STARTED": str(started)})
    env.pop("ANTHROPIC_API_KEY", None)

    print(f"workspace: {ws}")
    print("\n$ swarmkit eval ws four        # the third case hangs; the demo presses Ctrl-C")
    proc = subprocess.Popen(
        _swarmkit("eval", str(ws), "four"),
        env={**env, "HANG": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # its own process group, for the Ctrl-C below
    )
    deadline = time.monotonic() + 120
    while not started.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    # Ctrl-C in a terminal signals the whole foreground process group: the eval and the harness.
    # (Signalling the Python process alone leaves the harness running — #1109.)
    os.killpg(proc.pid, signal.SIGINT)
    out, err = proc.communicate(timeout=60)
    print(textwrap.indent(out.rstrip(), "  "))
    print(textwrap.indent("\n".join(ln for ln in err.splitlines() if "interrupted" in ln), "  "))
    print(f"  (exit {proc.returncode})")

    listing = _show(["eval-runs", "list", str(ws)], env)
    run_id = listing.split()[0]
    _show(["eval-runs", "resume", str(ws), run_id], env)
    _show(["eval-runs", "show", str(ws), run_id], env)


if __name__ == "__main__":
    main()
