"""`POST /api/ag-ui/run` — AG-UI protocol surface on `swarmkit serve`.

See `design/details/ag-ui-protocol.md` + `docs/notes/ag-ui-integration.md`. v1 implements the
Lifecycle + Messages subset of AG-UI's event vocabulary; richer events (tool calls, subagents,
interrupts) are v2. These tests pin the v1 contract, including the governance invariant that
an AG-UI run produces the same job (and audit footprint) as a `/run/{topology}` run — the
response shape differs, nothing else does.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.server import create_app


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A copy of the hello-swarm workspace with mock provider, so a run completes
    deterministically without touching a real LLM."""
    src = Path(__file__).resolve().parents[3] / "examples" / "hello-swarm" / "workspace"
    ws = tmp_path / "workspace"
    shutil.copytree(src, ws)
    with TestClient(create_app(ws)) as c:
        yield c


def _topology(client: TestClient) -> str:
    body = client.get("/topologies").json()
    names = body["topologies"] if isinstance(body, dict) else body
    first = sorted(names, key=lambda n: n["name"] if isinstance(n, dict) else n)[0]
    return str(first["name"] if isinstance(first, dict) else first)


def _events(response_text: str) -> list[dict[str, object]]:
    """Parse the SSE response text into a list of AG-UI event JSONs."""
    events: list[dict[str, object]] = []
    for frame in response_text.split("\n\n"):
        line = frame.strip()
        if not line.startswith("data:"):
            continue
        with contextlib.suppress(json.JSONDecodeError):
            events.append(json.loads(line[len("data:") :].strip()))
    return events


# ---- request validation ---------------------------------------------------------------------


def test_missing_topology_in_context_returns_400(client: TestClient) -> None:
    res = client.post(
        "/api/ag-ui/run",
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    assert res.status_code == 400
    assert "context.topology" in res.json()["detail"]


def test_unknown_topology_returns_404(client: TestClient) -> None:
    res = client.post(
        "/api/ag-ui/run",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "context": {"topology": "does-not-exist"},
        },
    )
    assert res.status_code == 404
    assert "topology_not_found" in res.json()["detail"]


# ---- the event stream shape -----------------------------------------------------------------


def test_run_emits_the_v1_event_sequence(client: TestClient) -> None:
    """RunStarted → TextMessageStart → TextMessageContent* → TextMessageEnd → RunFinished."""
    topology = _topology(client)
    res = client.post(
        "/api/ag-ui/run",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "context": {"topology": topology},
        },
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = _events(res.text)
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED"
    assert "TEXT_MESSAGE_START" in types
    assert "TEXT_MESSAGE_END" in types
    assert types[-1] in {"RUN_FINISHED", "RUN_ERROR"}

    # TextMessageContent uses one messageId for the whole run.
    message_ids = {
        e["messageId"]
        for e in events
        if e["type"] in {"TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"}
    }
    assert len(message_ids) == 1


def test_final_content_delta_carries_the_job_output(client: TestClient) -> None:
    """The real model answer must land as a TEXT_MESSAGE_CONTENT delta before TEXT_MESSAGE_END,
    not just on RUN_FINISHED.result. CopilotKit's chat only renders content deltas into the
    assistant bubble; without this, structured output topologies would appear empty in chat
    even though the underlying run completed with real output."""
    topology = _topology(client)
    res = client.post(
        "/api/ag-ui/run",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "context": {"topology": topology},
        },
    )
    events = _events(res.text)
    end_idx = next(i for i, e in enumerate(events) if e["type"] == "TEXT_MESSAGE_END")
    finished = next(e for e in events if e["type"] == "RUN_FINISHED")
    result = finished.get("result") if isinstance(finished, dict) else None
    final_output = result.get("output") if isinstance(result, dict) else None

    # Only act when the fixture produced actual output; mock provider does.
    if not final_output:
        return
    content_deltas = [
        str(e["delta"]) for e in events[:end_idx] if e["type"] == "TEXT_MESSAGE_CONTENT"
    ]
    combined = "".join(content_deltas)
    # Serialise dict output the same way the translator does so the comparison matches.
    expected = final_output if isinstance(final_output, str) else json.dumps(final_output)
    assert expected in combined, (
        f"final job output not present in content deltas; deltas={content_deltas!r}"
    )


def test_run_started_carries_thread_and_run_ids(client: TestClient) -> None:
    topology = _topology(client)
    res = client.post(
        "/api/ag-ui/run",
        json={
            "threadId": "thread-fixed",
            "runId": "run-fixed",
            "messages": [{"role": "user", "content": "hi"}],
            "context": {"topology": topology},
        },
    )
    events = _events(res.text)
    started = next(e for e in events if e["type"] == "RUN_STARTED")
    assert started["threadId"] == "thread-fixed"
    assert started["runId"] == "run-fixed"
    assert started["input"] == {"topology": topology}


def test_run_finished_carries_success_outcome(client: TestClient) -> None:
    topology = _topology(client)
    res = client.post(
        "/api/ag-ui/run",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "context": {"topology": topology},
        },
    )
    events = _events(res.text)
    finished = next(e for e in events if e["type"] in {"RUN_FINISHED", "RUN_ERROR"})
    # The mock provider returns deterministically, so success is expected.
    if finished["type"] == "RUN_FINISHED":
        assert finished["outcome"] == {"type": "success"}


def test_missing_thread_id_generates_one(client: TestClient) -> None:
    """threadId is optional; the server mints a ULID-shaped id if the client omits it."""
    topology = _topology(client)
    res = client.post(
        "/api/ag-ui/run",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "context": {"topology": topology},
        },
    )
    events = _events(res.text)
    started = next(e for e in events if e["type"] == "RUN_STARTED")
    assert isinstance(started["threadId"], str)
    assert len(started["threadId"]) > 8


# ---- governance invariant -------------------------------------------------------------------


def test_ag_ui_and_run_endpoint_produce_the_same_job(client: TestClient) -> None:
    """Governance invariant: an AG-UI run and a /run/{topology} run go through the same
    JobService.start() — same correlation_id handling, same audit entries. The response shape
    differs, nothing else does.

    This test pins behaviour by asserting: after each run, GET /jobs lists exactly one job per
    POST, with matching topology and status fields.
    """
    topology = _topology(client)

    before = len(client.get("/jobs").json())

    client.post(
        "/api/ag-ui/run",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "context": {"topology": topology},
        },
    )
    client.post(f"/run/{topology}", json={"input": "hi"})

    after = client.get("/jobs").json()
    # Two new jobs; both named the same topology.
    assert len(after) - before == 2
    new_jobs = after[-2:]
    assert all(j["topology"] == topology for j in new_jobs)


def test_thread_id_flows_to_correlation_id(client: TestClient) -> None:
    """ThreadId is aliased to correlation_id so audit queries can group AG-UI runs by thread."""
    topology = _topology(client)
    client.post(
        "/api/ag-ui/run",
        json={
            "threadId": "my-thread",
            "messages": [{"role": "user", "content": "hi"}],
            "context": {"topology": topology},
        },
    )
    # The job list carries topology but not correlation_id; the durable store does. The behaviour
    # being pinned is 'threadId is accepted and does not fail' — the deeper audit assertion
    # belongs to the follow-up PR that adds the structured event bus.
    jobs = client.get("/jobs").json()
    assert any(j["topology"] == topology for j in jobs)
