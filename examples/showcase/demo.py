#!/usr/bin/env python3
"""The SwarmKit story in one run, on the version you actually installed.

Nine steps, each printing what SwarmKit did and why it matters. No API keys and no network: the
mock model provider answers deterministically, so this is safe to run on a laptop with the wifi off
and it produces the same output every time — which is what you want in front of an audience.

    just demo-showcase                                              # starts serve itself
    python examples/showcase/demo.py --serve http://127.0.0.1:8000  # against a running one

Every claim is made by calling the public HTTP API — the same one an application uses. Nothing here
reaches into the runtime.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from typing import Any

import httpx

BOLD, DIM, GREEN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"


def step(n: int, title: str, why: str) -> None:
    print(f"\n{BOLD}{n}. {title}{RESET}\n   {DIM}{why}{RESET}")


def show(label: str, value: Any) -> None:
    print(f"   {label:<22} {value}")


class Serve:
    """`swarmkit serve` over HTTP — the whole SwarmKit-shaped surface of this demo."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.c = httpx.Client(timeout=60.0)

    def get(self, path: str, **params: Any) -> Any:
        r = self.c.get(f"{self.base}{path}", params=params or None)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        r = self.c.post(f"{self.base}{path}", json=body or {})
        r.raise_for_status()
        return r.json()

    def wait_for(self, job_id: str, status: str, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(f"/jobs/{job_id}")
            if job.get("status") == status:
                return dict(job)
            time.sleep(0.5)
        msg = f"job {job_id} never reached {status!r}"
        raise TimeoutError(msg)


def show_topology(sk: Serve) -> None:
    step(
        1,
        "The swarm is a file, not code",
        "Topology-as-data: the runtime interprets YAML. Nothing is generated or compiled.",
    )
    topo = sk.get("/api/topologies/release")
    root = topo.get("resolved") or {}
    names = [root.get("id", "?"), *[c.get("id", "?") for c in root.get("children") or []]]
    show("topology", f"{topo.get('id')} v{topo.get('version')}")
    show("agents", " -> ".join(names))
    show("gated by", f"{root.get('funnel')} (a human funnel)")


def show_decision(sk: Serve, item_id: str, job_id: str) -> None:
    step(
        8,
        "Who decided what, and when",
        "Append-only. No update or delete path is exposed to an agent, ever.",
    )
    # The decision is durable in the review record: who resolved it, with what comment, against
    # which artifact. Read from there rather than from the event stream because THIS demo runs
    # `governance: mock`, whose record_event does not persist — a provider that does (AGT) writes
    # approval.* into the same stream. Claiming an audit line the demo cannot show is exactly the
    # kind of thing a prospect checks.
    decided = sk.get(f"/review/{item_id}")
    show("decision", f"{GREEN}{decided.get('status')}{RESET}")
    show("resolved_by", decided.get("resolved_by") or "-")
    show("comment", decided.get("comment") or "-")
    show("artifact", decided.get("artifact_ref") or "-")

    print(f"\n   {DIM}and the run's own event trail:{RESET}")
    seen: set[str] = set()
    for e in sk.get("/events", run_id=job_id, limit=50)["events"]:
        # A gate is announced again when a resuming run re-enters the gated node, so a consumer —
        # and a demo — shows each kind once.
        if e["event_type"] in seen:
            continue
        seen.add(e["event_type"])
        mark = "  <--" if "gate" in e["event_type"] else ""
        print(f"   {DIM}{e['timestamp'][11:19]}{RESET}  {e['event_type']}{mark}")


def run_demo(sk: Serve) -> None:
    show_topology(sk)

    step(
        2,
        "Start a run",
        "One HTTP call. An application drives SwarmKit; SwarmKit does not drive the application.",
    )
    job_id = sk.post("/run/release", {"input": "Release 1.216.0: channels out, event seam in."})[
        "job_id"
    ]
    show("job", job_id)

    step(
        3,
        "It parks on the human gate",
        "Checkpointed and released — nothing stays resident while a person is at lunch.",
    )
    show("status", f"{YELLOW}{sk.wait_for(job_id, 'deferred')['status']}{RESET}")

    step(
        4,
        "The runtime says what happened",
        "An application taps GET /events. The runtime ships no chat integration of its own.",
    )
    events = sk.get("/events", types="funnel.gate_opened", run_id=job_id, limit=5)["events"]
    gate_id = events[0]["payload"]["gate_id"]
    show("event", events[0]["event_type"])
    show("gate_id", gate_id)
    show("cursor", events[0]["cursor"][:28] + "...")

    step(
        5,
        "What is it waiting for?",
        "The gate names the role and the scope. An agent can never hold release:approve.",
    )
    gate = sk.get(f"/gates/{gate_id}")
    show("outstanding", ", ".join(gate.get("outstanding") or []))
    show("resolved", gate.get("resolved"))

    step(
        6,
        "A person decides — anywhere",
        "Your application asked them on Slack, Telegram or a ticket. SwarmKit records only who.",
    )
    item = next(i for i in sk.get("/review") if i.get("gate_id") == gate_id)
    resolved = sk.post(
        f"/review/{item['id']}/resolve",
        {"outcome": "approve", "comment": "Checked the removal list. Approved."},
    )
    show("decision", f"{GREEN}{resolved['status']}{RESET}")
    show("resolved_by", resolved.get("resolved_by") or "(the authenticated caller)")

    step(
        7,
        "The run continues on its own",
        "A satisfied gate resumes its run. No application has to remember to call resume.",
    )
    show("status", f"{GREEN}{sk.wait_for(job_id, 'completed')['status']}{RESET}")

    show_decision(sk, item["id"], job_id)

    step(
        9,
        "Nothing was declared and left unwired",
        "The runtime compiles every topology and reports config no code path reaches.",
    )
    report = sk.get("/workspace/reachability")
    unreached = report.get("unreached") or report.get("unreached_declarations") or []
    show("unreached config", unreached or f"{GREEN}0{RESET}")

    print(f"\n{BOLD}That is the whole shape:{RESET} a swarm defined as data, doing work under")
    print("governance, pausing for a named human, resumed by their decision, with a durable")
    print("record of who approved what — and an application free to ask them anywhere.\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", default="", help="An already-running swarmkit serve.")
    ap.add_argument("--port", type=int, default=8123)
    args = ap.parse_args()

    if args.serve:
        run_demo(Serve(args.serve))
        return

    workspace = "examples/showcase/workspace"
    print(f"{DIM}starting swarmkit serve on :{args.port} (mock provider, no network needed){RESET}")
    # The console script, not `python -m swarmkit_runtime.cli` — that package has no __main__, so
    # the subprocess died instantly and the health loop below then polled nothing.
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
            # A health loop that gives up quietly and carries on produces a 404 nobody can read.
            msg = f"swarmkit serve never came up on {base}"
            raise RuntimeError(msg)
        run_demo(Serve(base))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
