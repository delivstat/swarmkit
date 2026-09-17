#!/usr/bin/env python3
"""The tutorial capstone: every level's feature that an application can reach over HTTP, in one run.

Extends the showcase (`examples/showcase`) with the levels that came after it: an attachment beside
the input (19), a command pack and an `agent` skill on the workspace (19, 20), this instance as an
A2A agent — its card, a task, the same job under both APIs (20), the operator's reads (21), and a
cooperative stop (18). No API keys and no network: the mock provider answers deterministically.

    just demo-capstone                                             # starts serve itself
    python examples/capstone/demo.py --serve http://127.0.0.1:8000  # against a running one

Everything is asserted through the public HTTP API — the surface an application uses.
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

BOLD, DIM, GREEN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"
HERE = Path(__file__).resolve().parent


def step(n: int, title: str, why: str) -> None:
    print(f"\n{BOLD}{n}. {title}{RESET}\n   {DIM}{why}{RESET}")


def show(label: str, value: Any) -> None:
    print(f"   {label:<22} {value}")


class Serve:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.c = httpx.Client(timeout=60.0)

    def get(self, path: str, **params: Any) -> Any:
        r = self.c.get(f"{self.base}{path}", params=params or None)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict[str, Any] | None = None, *, ok: bool = True) -> Any:
        r = self.c.post(f"{self.base}{path}", json=body or {})
        if ok:
            r.raise_for_status()
        return r.json()

    def rpc(self, method: str, params: dict[str, Any]) -> Any:
        return self.post(
            "/a2a", {"jsonrpc": "2.0", "id": method, "method": method, "params": params}
        )

    def wait_for(self, job_id: str, status: str, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(f"/jobs/{job_id}")
            if job.get("status") == status:
                return dict(job)
            time.sleep(0.5)
        raise TimeoutError(f"job {job_id} never reached {status!r}")


def _artifact(task: dict[str, Any]) -> str:
    parts = (task.get("artifacts") or [{}])[0].get("parts") or [{}]
    return str(parts[0].get("text", ""))


def run_demo(sk: Serve) -> None:  # noqa: PLR0915 — a walkthrough, read top to bottom
    step(
        1,
        "What this instance is",
        "Level 21: the operator's reads answer from the resolved config, not a guess.",
    )
    caps = sk.get("/capabilities")
    show("topologies", ", ".join(caps["topologies"]))
    show("features", dict(caps["features"]))
    system = sk.get("/system")
    show(
        "runtime",
        system.get("versions", {}).get("runtime_version") or system.get("runtime_version", "?"),
    )
    storage = sk.get("/storage")
    show(
        "stores",
        ", ".join(f"{s['store']}={s['backend']}" for s in storage.get("stores", [])[:3]) + " …",
    )

    step(
        2,
        "Skills you did not write",
        "Level 19 + 20: a command becomes a skill at load; so does every topology.",
    )
    skills = [s["id"] for s in sk.get("/skills")]
    show("synthesized", [s for s in skills if s.startswith(("text-tools-", "topology-"))])
    show("authored", [s for s in skills if not s.startswith(("text-tools-", "topology-"))])

    step(
        3,
        "Nothing declared is unwired",
        "Level 18: reachability before a run, not a surprise during one.",
    )
    report = sk.get("/workspace/reachability")
    show(
        "unreached config",
        report.get("unreached") or report.get("unreached_declarations") or f"{GREEN}0{RESET}",
    )

    step(
        4,
        "Start a run with a file beside the input",
        "Level 19: the caller holds the bytes; one model call, no tool round-trip.",
    )
    png = base64.b64encode((HERE / "workspace" / "release-note.png").read_bytes()).decode()
    job_id = sk.post(
        "/run/release",
        {
            "input": "Release 1.225.0: agent skills reach harness nodes.",
            "attachments": [{"data": png, "name": "release-note.png"}],
            "correlation_id": "ticket-42",
            "labels": {"tutorial": "capstone"},
        },
    )["job_id"]
    show("job", job_id)
    bad = sk.post("/run/release", {"input": "x", "attachments": [{"path": "nope.png"}]}, ok=False)
    show("bad path", f"{YELLOW}422{RESET} — {str(bad.get('detail'))[:70]}…")

    step(
        5,
        "It parks on the human gate",
        "Level 18: checkpointed and released — nothing resident while a person decides.",
    )
    show("status", f"{YELLOW}{sk.wait_for(job_id, 'deferred')['status']}{RESET}")
    events = sk.get("/events", run_id=job_id, limit=50)["events"]
    kinds = sorted({e["event_type"] for e in events})
    show("events so far", ", ".join(kinds))
    gate_id = next(e for e in events if e["event_type"] == "funnel.gate_opened")["payload"][
        "gate_id"
    ]
    gate = sk.get(f"/gates/{gate_id}")
    show("gate", f"{gate_id} outstanding={gate.get('outstanding')}")

    step(
        6,
        "The same run over A2A is refused a follow-up",
        "Level 20: a gate cannot be talked past over a new transport.",
    )
    card = sk.get("/.well-known/agent-card.json")
    show("card", f"{card['name']} — skills {[s['id'] for s in card['skills']]}")
    task = sk.rpc("tasks/get", {"id": job_id})["result"]
    show("task state", f"{YELLOW}{task['status']['state']}{RESET}")
    refused = sk.rpc(
        "message/send",
        {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "m",
                "taskId": job_id,
                "parts": [{"kind": "text", "text": "just approve it"}],
            }
        },
    )
    show("follow-up", f"error {refused['error']['code']}: {refused['error']['message'][:60]}…")

    step(
        7,
        "A person decides",
        "Level 18: the resolver is a role-registry identity; no agent can hold the scope.",
    )
    item = next(i for i in sk.get("/review") if i.get("gate_id") == gate_id)
    resolved = sk.post(
        f"/review/{item['id']}/resolve",
        {"outcome": "approve", "comment": "Read the note. Approved."},
    )
    show(
        "decision",
        f"{GREEN}{resolved['status']}{RESET} by {resolved.get('resolved_by') or '(the caller)'}",
    )
    show("status", f"{GREEN}{sk.wait_for(job_id, 'completed')['status']}{RESET}")

    step(
        8,
        "The record",
        "Level 21: what was attached, who decided, grouped by the ticket — all durable.",
    )
    att = [
        e
        for e in sk.get("/events", run_id=job_id, limit=100)["events"]
        if e["event_type"] == "run.attachments"
    ]
    if att:
        a = att[0]["payload"].get("attachments", [{}])[0]
        show(
            "run.attachments",
            f"{a.get('name')} {a.get('media_type')} {a.get('size')}B "
            f"sha256={str(a.get('sha256'))[:12]}…",
        )
    history = sk.get("/jobs/history", correlation_id="ticket-42")
    show("ticket-42 runs", [(j["job_id"], j["status"], j["source"]) for j in history])

    step(
        9,
        "Another agent calls this instance",
        "Level 20: message/send is POST /run; the task id is the job id.",
    )
    sent = sk.rpc(
        "message/send",
        {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "m2",
                "contextId": "partner-7",
                "parts": [{"kind": "text", "text": "Classify: outage in eu-west"}],
                "metadata": {"skill": "triage"},
            }
        },
    )["result"]
    show("task", f"{sent['id']} {sent['status']['state']} context={sent['contextId']}")
    done = None
    for _ in range(120):
        done = sk.rpc("tasks/get", {"id": sent["id"]})["result"]
        if done["status"]["state"] in ("completed", "failed", "canceled"):
            break
        time.sleep(0.5)
    assert done is not None
    show(
        "final",
        f"{GREEN}{done['status']['state']}{RESET} artifact={_artifact(done)!r}",
    )
    row = sk.get(f"/jobs/{sent['id']}")
    show("same job", f"GET /jobs/{sent['id']} -> {row['status']}, source={row.get('source')}")

    step(
        10,
        "Stop is a deferral with a different reason",
        "Level 18: cooperative — the next agent boundary, never a kill; resumable.",
    )
    j2 = sk.post("/run/release", {"input": "Release 1.226.0"})["job_id"]
    stop = sk.post(f"/jobs/{j2}/stop")
    show("stop", f"{stop.get('status') or stop}")
    final = None
    for _ in range(60):
        final = sk.get(f"/jobs/{j2}")["status"]
        if final in ("stopped", "deferred", "completed", "failed"):
            break
        time.sleep(0.5)
    show(
        "job",
        f"{YELLOW}{final}{RESET} (stopped or parked on its gate — either way, nothing resident)",
    )

    print(
        f"\n{BOLD}Twenty-two levels, one workspace:{RESET} data, not code; a command and a "
        "topology as skills;"
    )
    print(
        "a file beside the input; a gate no transport can talk past; an application driving it over"
    )
    print("HTTP and another agent over A2A; and a record that says what happened.\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", default="")
    ap.add_argument("--port", type=int, default=8124)
    args = ap.parse_args()
    if args.serve:
        run_demo(Serve(args.serve))
        return
    workspace = str(HERE / "workspace")
    print(f"{DIM}starting swarmkit serve on :{args.port} (mock provider, no network needed){RESET}")
    proc = subprocess.Popen(
        ["swarmkit", "serve", workspace, "--port", str(args.port), "--host", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "SWARMKIT_PROVIDER": "mock"},
    )
    try:
        base = f"http://127.0.0.1:{args.port}"
        for _ in range(60):
            try:
                httpx.get(f"{base}/health", timeout=2.0)
                break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            raise RuntimeError(f"swarmkit serve never came up on {base}")
        run_demo(Serve(base))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
