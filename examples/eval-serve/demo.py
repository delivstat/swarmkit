"""Eval sets edited, run, interrupted, resumed and diffed over serve's HTTP API.

    just demo-eval-serve

Builds a throwaway workspace with a three-case eval set against a one-node harness topology and
starts `swarmkit serve` on it. The harness is a fake `claude` on PATH: it answers "done" at once,
except that it hangs on case three while the demo wants it to, and gets case two wrong once the demo
flips a switch. Then, all over HTTP:

1. `PUT /api/evals/four` adds a fourth case; `POST /evals/four/run` starts a run (202);
2. while case three hangs, the serve is stopped (SIGINT) — the eval marks itself `interrupted`;
3. a new serve starts; `POST /evals/runs/{id}/resume` runs only cases three and four;
4. the harness starts getting case two wrong; a second run's diff names the regression;
5. `swarmkit eval-runs list` on the CLI lists both runs, from the same store.

A SIGKILL or power cut is the same story, except the row stays `running` until its heartbeat is
five minutes stale and the next serve's startup sweep takes it (tests/test_eval_routes.py,
tests/test_eval_kill_recovery.py).

No network beyond localhost and no API key: the harness is fake and governance is the mock
provider. Needs git and bash.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_FAKE_CLAUDE = """\
#!/usr/bin/env bash
echo '{"type":"system","session_id":"demo"}'
answer=done
case "$*" in *case-three*) [ -f "$HANG" ] && { touch "$STARTED"; sleep 600; } ;; esac
case "$*" in *case-two*) [ -f "$BREAK" ] && answer=oops ;; esac
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"%s"}]}}\n' "$answer"
printf '{"type":"result","subtype":"success","result":"%s","total_cost_usd":0.002}\n' "$answer"
"""

_CASES = """\
  - {id: one, input: do case-one, expect: {contains: [done]}}
  - {id: two, input: do case-two, expect: {contains: [done]}}
  - {id: three, input: do case-three, expect: {contains: [done]}}
"""

_SET = (
    """\
apiVersion: swarmkit/v1
kind: EvalSet
metadata: {id: four, description: The change topology answers each case.}
target: change
cases:
"""
    + _CASES
)

_FILES = {
    "workspace.yaml": """\
        apiVersion: swarmkit/v1
        kind: Workspace
        metadata: {id: eval-serve-demo, name: Eval serve demo}
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
}


def _swarmkit(*args: str) -> list[str]:
    return [sys.executable, "-c", "from swarmkit_runtime.cli import app; app()", *args]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class _Serve:
    def __init__(self, ws: Path, env: dict[str, str]) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen(
            _swarmkit("serve", str(ws), "--host", "127.0.0.1", "--port", str(self.port)),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # its own group, so a harness left behind can be reaped
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"{self.base}/health", timeout=1)
                return
            except (urllib.error.URLError, ConnectionError):
                if self.proc.poll() is not None:
                    err = self.proc.stderr.read() if self.proc.stderr else ""
                    raise SystemExit(f"serve exited:\n{err}") from None
                time.sleep(0.2)
        raise SystemExit("serve did not come up")

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def show(self, method: str, path: str, body: Any = None) -> Any:
        status, out = self.call(method, path, body)
        print(f"\n$ {method} {path}  -> {status}")
        return out

    def stop(self) -> None:
        """SIGINT to serve alone: it shuts down, and the eval in flight marks itself interrupted.

        Not the whole process group, as a terminal's Ctrl-C would be: that kills the harness too,
        a moment before serve begins shutting down, and the harness's "exit -2" is scored as the
        case's answer (#1111). The harness serve leaves behind (#1109) is reaped afterwards.
        """
        self.proc.send_signal(signal.SIGINT)
        self.proc.wait(timeout=60)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.proc.pid, signal.SIGKILL)

    def wait_for(self, run_id: str) -> dict[str, Any]:
        while True:
            _, row = self.call("GET", f"/evals/runs/{run_id}")
            if row["status"] != "running":
                return dict(row)
            time.sleep(0.3)


def _line(row: dict[str, Any]) -> str:
    return (
        f"  {row['status']:<11} {row['passed']}/{row['total']} passed · {row['recorded']} recorded"
        f" · ${row['cost_usd']:.3f} · source {row['source']}"
    )


def _setup() -> tuple[Path, Path, dict[str, str]]:
    """The workspace, the fake harness on PATH, and the env both serves and the CLI run with."""
    root = Path(tempfile.mkdtemp(prefix="eval-serve-demo-"))
    ws = root / "ws"
    for rel, text in _FILES.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(textwrap.dedent(text))
    (ws / "evals").mkdir()
    (ws / "evals" / "four.yaml").write_text(_SET)
    ident = ["-c", "user.email=demo@example.com", "-c", "user.name=demo"]
    for git in (["init", "-q", "-b", "main"], [*ident, "add", "."], [*ident, "commit", "-qm", "x"]):
        subprocess.run(["git", *git], cwd=ws, check=True)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(_FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    hang, brk, started = root / "hang", root / "break", root / "started"

    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    env.update(
        {"SWARMKIT_PROVIDER": "mock", "HANG": str(hang), "BREAK": str(brk), "STARTED": str(started)}
    )
    env.pop("ANTHROPIC_API_KEY", None)
    return root, ws, env


def main() -> None:
    root, ws, env = _setup()
    hang, brk, started = root / "hang", root / "break", root / "started"
    print(f"workspace: {ws}")

    serve = _Serve(ws, env)
    print(f"serve: {serve.base}")
    sets = serve.show("GET", "/api/evals")
    print(f"  {sets[0]['id']}: {sets[0]['case_count']} cases → {sets[0]['target']}")
    four = _SET + "  - {id: four, input: do case-four, expect: {contains: [done]}}\n"
    print(f"  {serve.show('PUT', '/api/evals/four', {'yaml': four})}")

    hang.touch()
    started_run = serve.show("POST", "/evals/four/run")
    print(f"  {started_run}")
    run_id = started_run["eval_run_id"]
    while not started.exists():
        time.sleep(0.2)
    print(
        f"\n(case three is hanging; {_line(serve.call('GET', f'/evals/runs/{run_id}')[1]).strip()})"
    )
    print("(stopping the serve: SIGINT)")
    serve.stop()
    hang.unlink()

    serve = _Serve(ws, env)
    print(f"\nserve restarted: {serve.base}")
    print(_line(serve.show("GET", f"/evals/runs/{run_id}")))
    print(f"  {serve.show('POST', f'/evals/runs/{run_id}/resume')}")
    done = serve.wait_for(run_id)
    print(f"\n$ GET /evals/runs/{run_id}  (polled until it finished)")
    print(_line(done))
    for case in done["report"]["cases"]:
        print(f"    [{'PASS' if case['passed'] else 'FAIL'}] {case['case_id']}")

    brk.touch()
    print("\n(the harness now gets case two wrong)")
    second = serve.show("POST", "/evals/four/run")["eval_run_id"]
    regressed = serve.wait_for(second)
    print(f"\n$ GET /evals/runs/{second}  (polled until it finished)")
    print(_line(regressed))
    diff = regressed["diff"]
    print(f"  diff against {diff['prior_eval_run_id']}:")
    print(f"    regressed {diff['regressed']} · fixed {diff['fixed']} · new {diff['new']}")
    serve.stop()

    print(f"\n$ swarmkit eval-runs list {ws}")
    out = subprocess.run(
        _swarmkit("eval-runs", "list", str(ws)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    print(textwrap.indent(out.stdout.rstrip(), "  "))


if __name__ == "__main__":
    main()
