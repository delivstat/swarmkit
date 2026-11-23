"""Recorded runs become eval cases, and catch a regression (design/details/eval-capture.md).

    just demo-eval-capture

1. `swarmkit serve` on a one-node triage topology runs four tickets over `POST /run`. One ticket
   repeats an earlier one.
2. `POST /evals/capture` previews the set: one case per run with the answer as its `reference`,
   and the repeated ticket skipped as `duplicate`. Nothing is written.
3. `POST /api/evals/capture` writes it, with `redact: ["DC-\\d+"]`, and the file has `[REDACTED]`
   where the warehouse code was.
4. `POST /evals/{id}/run` scores the unchanged topology against the captured cases: all pass.
5. The topology starts answering one ticket differently. A second run names that case regressed.
6. `swarmkit eval-capture --run <id>` on the CLI proposes the same case from the same store.

The topology is a fake `claude` harness that answers from a table, so the runs cost nothing. The
`reference` check's judge is a real model: the workspace's authoring provider, chosen the usual way
(SWARMKIT_AUTHOR_MODEL, else SWARMKIT_PROVIDER + SWARMKIT_MODEL, else the first provider with a
key). It makes six short judge calls. Needs git and one provider key.
"""

from __future__ import annotations

import json
import os
import re
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

_ANSWERS = {
    "charged twice": "Routed to billing. A duplicate charge is refunded within five days.",
    "site is down": "Escalated as a P1 outage to the on-call engineer.",
    "return for order": "Return logged for order 88 at DC-14; a label is emailed within an hour.",
}

#: A stand-in for `claude -p ... --output-format stream-json`: the answer is looked up by the
#: ticket text in $ANSWERS, a JSON file the demo edits to change the topology's behaviour.
_FAKE_CLAUDE = """#!{python}
import json, os, sys
prompt = sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else ""
answers = json.load(open(os.environ["ANSWERS"]))
text = next((a for key, a in answers.items() if key in prompt), "I could not route this ticket.")
print(json.dumps({{"type": "system", "session_id": "demo"}}))
block = {{"type": "text", "text": text}}
print(json.dumps({{"type": "assistant", "message": {{"content": [block]}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "result": text, "total_cost_usd": 0.0}}))
"""

_FILES = {
    "workspace.yaml": """\
        apiVersion: swarmkit/v1
        kind: Workspace
        metadata: {id: eval-capture-demo, name: Eval capture demo}
        governance: {provider: mock}
        memory: {enabled: false}
        """,
    "archetypes/triager.yaml": """\
        apiVersion: swarmkit/v1
        kind: Archetype
        metadata: {id: triager, name: Triager, description: A harness node.}
        role: root
        executor: {kind: harness, ref: claude-code}
        defaults:
          prompt: {system: You route support tickets.}
        provenance: {authored_by: human, version: 1.0.0}
        """,
    "topologies/triage.yaml": """\
        apiVersion: swarmkit/v1
        kind: Topology
        metadata: {name: triage, version: 0.1.0}
        agents:
          root: {id: triager, role: root, archetype: triager}
        """,
}

_KEYS = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY")


def _swarmkit(*args: str) -> list[str]:
    return [sys.executable, "-c", "from swarmkit_runtime.cli import app; app()", *args]


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class _Serve:
    def __init__(self, ws: Path, env: dict[str, str]) -> None:
        self.base = f"http://127.0.0.1:{_port()}"
        port = self.base.rsplit(":", 1)[1]
        self.proc = subprocess.Popen(
            _swarmkit("serve", str(ws), "--host", "127.0.0.1", "--port", port),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"{self.base}/health", timeout=1)
                return
            except (urllib.error.URLError, ConnectionError):
                if self.proc.poll() is not None:
                    raise SystemExit(
                        f"serve exited:\n{self.proc.stderr.read() if self.proc.stderr else ''}"
                    ) from None
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
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def run(self, text: str) -> str:
        _, job = self.call("POST", "/run/triage", {"input": text})
        job_id = str(job["job_id"])
        while self.call("GET", f"/jobs/{job_id}")[1]["status"] not in ("completed", "failed"):
            time.sleep(0.2)
        return job_id

    def eval_run(self, set_id: str) -> dict[str, Any]:
        _, started = self.call("POST", f"/evals/{set_id}/run")
        run_id = started["eval_run_id"]
        while True:
            _, row = self.call("GET", f"/evals/runs/{run_id}")
            if row["status"] != "running":
                return dict(row)
            time.sleep(0.3)

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=30)


def _setup() -> tuple[Path, Path, dict[str, str]]:
    root = Path(tempfile.mkdtemp(prefix="eval-capture-demo-"))
    ws = root / "ws"
    for rel, text in _FILES.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(textwrap.dedent(text))
    ident = ["-c", "user.email=demo@example.com", "-c", "user.name=demo"]
    for git in (["init", "-q", "-b", "main"], [*ident, "add", "."], [*ident, "commit", "-qm", "x"]):
        subprocess.run(["git", *git], cwd=ws, check=True)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(_FAKE_CLAUDE.format(python=sys.executable))
    (bin_dir / "claude").chmod(0o755)
    answers = root / "answers.json"
    answers.write_text(json.dumps(_ANSWERS))
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    env["ANSWERS"] = str(answers)
    return root, ws, env


def _summary(proposal: dict[str, Any]) -> None:
    print(f"  added   {', '.join(proposal['added']) or 'none'}")
    for note in proposal["skipped"]:
        print(f"  skipped {note['run_id'][:8]}…  {note['reason']}")


def main() -> int:
    if not any(os.environ.get(k) for k in _KEYS):
        print(f"The reference check's judge is a real model: set one of {', '.join(_KEYS)}.")
        return 2
    _root, ws, env = _setup()
    serve = _Serve(ws, env)
    print(f"serve: {serve.base}\n")

    tickets = [
        "Ticket: I was charged twice this month.",
        "Ticket: the site is down for everyone.",
        "Ticket: start a DC-14 return for order 88.",
        "Ticket: I was charged twice this month.",  # the same ticket again
    ]
    ids = [serve.run(t) for t in tickets]
    print(f"1. ran {len(ids)} tickets through POST /run/triage")

    body = {"topology": "triage", "into": "triage-regressions"}
    status, preview = serve.call("POST", "/evals/capture", body)
    print(f"\n2. POST /evals/capture  -> {status}   (preview, writes nothing)")
    _summary(preview)
    import yaml  # noqa: PLC0415

    first_case = yaml.safe_load(preview["yaml"])["cases"][0]
    print(textwrap.indent(yaml.safe_dump(first_case, sort_keys=False, width=100), "     "))

    status, written = serve.call("POST", "/api/evals/capture", {**body, "redact": [r"DC-\d+"]})
    text = (ws / "evals" / "triage-regressions.yaml").read_text()
    print(
        f"\n3. POST /api/evals/capture (redact DC-\\d+)  -> {status}, written={written['written']}"
    )
    print(f"  file has [REDACTED]: {'[REDACTED]' in text} · contains 'DC-14': {'DC-14' in text}")

    first = serve.eval_run("triage-regressions")
    score = f"{first['status']} {first['passed']}/{first['total']}"
    print(f"\n4. POST /evals/triage-regressions/run  -> {score}")
    for case in first["report"]["cases"]:
        print(f"    [{'PASS' if case['passed'] else 'FAIL'}] {case['case_id']}")

    changed = {**_ANSWERS, "site is down": "Closed as a duplicate of an older ticket."}
    Path(env["ANSWERS"]).write_text(json.dumps(changed))
    second = serve.eval_run("triage-regressions")
    print("\n5. the topology now closes the outage ticket instead of escalating it")
    print(f"   second run -> {second['status']} {second['passed']}/{second['total']}")
    for case in second["report"]["cases"]:
        mark = "PASS" if case["passed"] else "FAIL"
        why = next((c["detail"] for c in case["checks"] if not c["passed"]), "")
        print(f"    [{mark}] {case['case_id']}  {why[:90]}".rstrip())
    print(f"   diff: regressed {second['diff']['regressed']}")
    serve.stop()

    out = subprocess.run(
        _swarmkit("eval-capture", str(ws), "--run", ids[1], "--into", "cli-check", "--json"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    cli_case = json.loads(out.stdout)["added"]
    print(f"\n6. swarmkit eval-capture --run {ids[1][:8]}… --into cli-check  -> {cli_case}")
    same = re.sub(r"captured_at: .*", "", json.loads(out.stdout)["yaml"]).count(ids[1])
    print(f"   same run, same case id, read from the same store: {bool(same)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
