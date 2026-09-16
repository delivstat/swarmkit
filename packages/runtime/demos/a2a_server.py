"""Demo: the A2A server — an Agent Card and the task API as a transport onto jobs.

Runs hello-swarm under the mock provider with `server.a2a.enabled: true`, then acts as an A2A
client: fetch the card, `message/send`, watch `tasks/get`, `tasks/list`, and stream a second run
over `message/stream`. Everything it shows is the same job `GET /jobs/{id}` would show — the
task id IS the job id (design/details/a2a-interop.md).

    uv run python packages/runtime/demos/a2a_server.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]


def _rpc(client: TestClient, method: str, params: dict[str, Any]) -> Any:
    body = {"jsonrpc": "2.0", "id": method, "method": method, "params": params}
    return client.post("/a2a", json=body).json()


def _message(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "message",
        "role": "user",
        "messageId": "m-" + str(int(time.time() * 1000)),
        "parts": [{"kind": "text", "text": text}],
        "metadata": {"skill": "hello"},
        **extra,
    }


def main() -> None:  # noqa: PLR0915 — a walkthrough, read top to bottom
    os.environ.setdefault("SWARMKIT_PROVIDER", "mock")
    logging.disable(logging.INFO)  # the server's request log would drown the transcript
    ws = Path(tempfile.mkdtemp()) / "workspace"
    shutil.copytree(REPO / "examples/hello-swarm/workspace", ws)
    shutil.rmtree(ws / ".swarmkit", ignore_errors=True)  # a fresh store, not the example's history
    manifest = ws / "workspace.yaml"
    manifest.write_text(
        manifest.read_text()
        + "\nserver:\n  a2a:\n    enabled: true\n    identity:\n      name: Hello desk\n"
        "      organization: Example Org\n"
    )

    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(ws)) as client:
        print("== GET /.well-known/agent-card.json (public, no auth) ==")
        card = client.get("/.well-known/agent-card.json").json()
        print(
            json.dumps({k: card[k] for k in ("name", "url", "provider", "capabilities")}, indent=2)
        )
        print("skills:", [s["id"] for s in card["skills"]])

        print("\n== message/send (contextId groups runs; it rides on correlation_id) ==")
        sent = _rpc(
            client, "message/send", {"message": _message("Greet engineers", contextId="ticket-42")}
        )
        task = sent["result"]
        print(f"task {task['id']}  state={task['status']['state']}  context={task['contextId']}")

        print("\n== the same job through the job API ==")
        job = client.get(f"/jobs/{task['id']}").json()
        print(f"GET /jobs/{task['id']} -> status={job['status']}")

        print("\n== tasks/get until terminal ==")
        for _ in range(200):
            got = _rpc(client, "tasks/get", {"id": task["id"]})["result"]
            state = got["status"]["state"]
            if state in ("completed", "failed", "canceled"):
                break
            time.sleep(0.1)
        print(f"state={state}")
        if got.get("artifacts"):
            part = got["artifacts"][0]["parts"][0]
            print("artifact part kind:", part["kind"])
            print("artifact:", json.dumps(part.get("data") or part.get("text"))[:200])

        print("\n== a follow-up message on the task is refused (a new message is a new run) ==")
        again = _rpc(client, "message/send", {"message": _message("more?", taskId=task["id"])})
        print(f"error {again['error']['code']}: {again['error']['message']}")

        print("\n== tasks/list (source=a2a only; a POST /run job is not listed) ==")
        client.post("/run/hello", json={"input": "over the job api"})
        listed = _rpc(client, "tasks/list", {})["result"]["tasks"]
        print([(t["id"], t["status"]["state"], t["contextId"]) for t in listed])
        history = client.get("/jobs/history").json()
        print("jobs/history sources:", {j["job_id"]: j["source"] for j in history})

        print("\n== message/stream (SSE) ==")
        with client.stream(
            "POST",
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "s",
                "method": "message/stream",
                "params": {"message": _message("stream me")},
            },
            headers={"accept": "text/event-stream"},
        ) as resp:
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                frame = json.loads(line[6:])["result"]
                if frame["kind"] == "task":
                    print(f"  task {frame['id']} {frame['status']['state']}")
                elif frame["kind"] == "artifact-update":
                    print("  artifact-update")
                else:
                    ev = frame.get("metadata", {}).get("swarmkit", {}).get("event")
                    print(
                        f"  status-update {frame['status']['state']} final={frame['final']}"
                        + (f"  event={str(ev)[:60]}" if ev else "")
                    )

        print("\n== push notifications are not supported ==")
        pn = _rpc(client, "tasks/pushNotificationConfig/set", {})
        print(f"error {pn['error']['code']}: {pn['error']['message']}")


if __name__ == "__main__":
    main()
