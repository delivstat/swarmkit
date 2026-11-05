"""A real serve with eval history, for the Evals page screenshots (`screenshot_evals.mjs`).

    uv run python packages/ui/demos/seed_eval_workspace.py [port]

Builds a throwaway workspace with a four-case eval set against a one-node harness topology, starts
`swarmkit serve` on it, and runs the set once over HTTP so the page opens with a baseline run
(4/4). Then it prints one JSON line — `{"base", "hang", "break"}` — and keeps serve up until it is
interrupted.

The harness is a fake `claude` on PATH, steered by two files the capture script touches:

- while `hang` exists, case three waits (a loop, not a sleep, so removing the file lets it finish
  and a stopped run can be shown stopping after the case in flight);
- while `break` exists, case two answers wrongly — the regression the second run's diff names.

The portal is whatever `swarmkit_webui/_static` holds: build it first (`pnpm --filter @swarmkit/ui
build`, then copy `out/` there). No network beyond localhost and no API key: the harness is fake
and governance is the mock provider. Same workspace shape as `examples/eval-serve/demo.py`.
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
case "$*" in *case-three*) while [ -f "$HANG" ]; do sleep 0.2; done ;; esac
case "$*" in *case-two*) [ -f "$BREAK" ] && answer=oops ;; esac
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"%s"}]}}\\n' "$answer"
printf '{"type":"result","subtype":"success","result":"%s","total_cost_usd":0.002}\\n' "$answer"
"""

_SET = """\
apiVersion: swarmkit/v1
kind: EvalSet
metadata: {id: four, description: The change topology answers each case.}
target: change
cases:
  - {id: one, input: do case-one, expect: {contains: [done]}}
  - {id: two, input: do case-two, expect: {contains: [done], not_contains: [oops]}}
  - {id: three, input: do case-three, expect: {contains: [done]}}
  - {id: four, input: do case-four, expect: {contains: [done], not_empty: true}}
"""

_FILES = {
    "workspace.yaml": """\
        apiVersion: swarmkit/v1
        kind: Workspace
        metadata: {id: eval-portal-demo, name: Eval portal demo}
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


def _call(base: str, method: str, path: str) -> Any:
    req = urllib.request.Request(f"{base}{path}", method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"null")


def _exit(signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8097
    root = Path(tempfile.mkdtemp(prefix="eval-portal-demo-"))
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
    hang, brk = root / "hang", root / "break"

    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    env.update({"SWARMKIT_PROVIDER": "mock", "HANG": str(hang), "BREAK": str(brk)})
    env.pop("ANTHROPIC_API_KEY", None)

    base = f"http://127.0.0.1:{port}"
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"port {port} is in use")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from swarmkit_runtime.cli import app; app()",
            "serve",
            str(ws),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"{base}/health", timeout=1)
                break
            except (urllib.error.URLError, ConnectionError):
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise SystemExit("serve did not come up") from None
                time.sleep(0.2)

        baseline = _call(base, "POST", "/evals/four/run")["eval_run_id"]
        while _call(base, "GET", f"/evals/runs/{baseline}")["status"] == "running":
            time.sleep(0.3)

        print(json.dumps({"base": base, "hang": str(hang), "break": str(brk)}), flush=True)
        # Both signals end it the same way, through the `finally` that takes serve down with it. A
        # seed started in the background of a script has SIGINT ignored, so SIGTERM must work too.
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _exit)
        with contextlib.suppress(KeyboardInterrupt):
            proc.wait()
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)


if __name__ == "__main__":
    main()
