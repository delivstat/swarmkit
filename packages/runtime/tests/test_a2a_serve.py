"""A2A server — the Agent Card and the JSON-RPC task API as a transport onto jobs.

design/details/a2a-interop.md. Unit tests hold the card and the state mapping; the integration
tests run the hello-swarm workspace under the mock provider through ``message/send``,
``tasks/get``, ``tasks/list``, ``tasks/cancel`` and ``message/stream``, and check that a run
started over A2A is the same job ``GET /jobs/{id}`` sees.
"""

from __future__ import annotations

import json
import shutil
import time
import typing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.server._a2a import (
    CONTENT_TYPE_NOT_SUPPORTED,
    INVALID_PARAMS,
    INVALID_REQUEST,
    JOB_TO_TASK_STATE,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PUSH_NOTIFICATION_NOT_SUPPORTED,
    SOURCE,
    TASK_NOT_CANCELABLE,
    TASK_NOT_FOUND,
    TERMINAL_STATES,
    UNSUPPORTED_OPERATION,
    WELL_KNOWN_PATH,
    build_agent_card,
    message_attachments,
    message_text,
    task_from_job,
)
from swarmkit_runtime.server._config import ServerCfg, _parse_server_config
from swarmkit_runtime.server._helpers import _required_action
from swarmkit_runtime.server._jobs import Job

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def a2a_workspace(tmp_path: Path) -> Path:
    """hello-swarm with `server.a2a.enabled: true` and an identity block."""
    ws = tmp_path / "workspace"
    shutil.copytree(EXAMPLE_WS, ws, ignore=shutil.ignore_patterns(".swarmkit"))
    manifest = ws / "workspace.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + "\nserver:\n  a2a:\n    enabled: true\n    identity:\n"
        "      name: Hello desk\n      organization: Example Org\n",
        encoding="utf-8",
    )
    return ws


@pytest.fixture()
def client(a2a_workspace: Path) -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(a2a_workspace)) as c:
        yield c


@pytest.fixture()
def disabled_client(tmp_path: Path) -> TestClient:  # type: ignore[misc]
    """hello-swarm as shipped (no `server.a2a`), on its own copy: two workers opening the example's
    live store at once race on table creation."""
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    ws = tmp_path / "plain"
    shutil.copytree(EXAMPLE_WS, ws, ignore=shutil.ignore_patterns(".swarmkit"))
    with TestClient(create_app(ws)) as c:
        yield c


def _rpc(client: TestClient, method: str, params: dict[str, Any], *, path: str = "/a2a") -> Any:
    resp = client.post(path, json={"jsonrpc": "2.0", "id": "1", "method": method, "params": params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _send(client: TestClient, text: str, *, skill: str = "hello", **extra: Any) -> Any:
    message = {
        "kind": "message",
        "role": "user",
        "messageId": "m1",
        "parts": [{"kind": "text", "text": text}],
        "metadata": {"skill": skill},
        **extra,
    }
    return _rpc(client, "message/send", {"message": message})


def _wait_terminal(client: TestClient, task_id: str, timeout: float = 20.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = _rpc(client, "tasks/get", {"id": task_id})
        state = body["result"]["status"]["state"]
        if state in TERMINAL_STATES:
            return body["result"]
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} did not finish")


# ---- unit: mapping ------------------------------------------------------------------------------


def test_state_mapping_is_exhaustive_over_job_status() -> None:
    """Every `Job.status` literal maps to exactly one A2A state; a new status must be placed."""
    literal = typing.get_type_hints(Job)["status"]
    statuses = set(typing.get_args(literal))
    assert statuses == set(JOB_TO_TASK_STATE)
    assert set(JOB_TO_TASK_STATE.values()) <= {
        "submitted",
        "working",
        "input-required",
        "completed",
        "failed",
        "canceled",
    }


def _job(status: str, **kw: Any) -> Job:
    job = Job(
        id="job-1",
        topology="hello",
        input="hi",
        status=status,  # type: ignore[arg-type]
        created_at=datetime.now(UTC).isoformat(),
    )
    for k, v in kw.items():
        setattr(job, k, v)
    return job


def test_task_from_completed_job_carries_the_output_as_an_artifact() -> None:
    task = task_from_job(_job("completed", output={"answer": 42}))
    assert task["kind"] == "task"
    assert task["id"] == "job-1"
    assert task["status"]["state"] == "completed"
    parts = task["artifacts"][0]["parts"]
    assert parts[0]["kind"] == "data"
    assert parts[0]["data"] == {"answer": 42}


def test_task_from_failed_job_names_the_error() -> None:
    task = task_from_job(_job("failed", error="boom"))
    assert task["status"]["state"] == "failed"
    assert "boom" in task["status"]["message"]["parts"][0]["text"]


def test_task_from_deferred_job_is_input_required_with_the_gate_named() -> None:
    task = task_from_job(_job("deferred"), gate_url="http://x/gates/job-1:writer")
    assert task["status"]["state"] == "input-required"
    text = task["status"]["message"]["parts"][0]["text"]
    assert "human" in text
    assert "http://x/gates/job-1:writer" in text
    assert task["metadata"]["swarmkit"]["gate_url"] == "http://x/gates/job-1:writer"


def test_message_text_concatenates_text_and_data_parts() -> None:
    msg = {
        "parts": [
            {"kind": "text", "text": "hello"},
            {"kind": "data", "data": {"k": 1}},
            {"kind": "file", "file": {"bytes": "aGk=", "name": "a.txt"}},
        ]
    }
    text = message_text(msg)
    assert text.startswith("hello")
    assert '"k": 1' in text
    files = message_attachments(msg)
    assert files and files[0]["name"] == "a.txt"


def test_uri_file_parts_are_refused() -> None:
    from swarmkit_runtime.server._a2a import A2AError  # noqa: PLC0415

    with pytest.raises(A2AError) as exc:
        message_attachments({"parts": [{"kind": "file", "file": {"uri": "https://x/y.png"}}]})
    assert exc.value.code == CONTENT_TYPE_NOT_SUPPORTED


# ---- unit: card + config --------------------------------------------------------------------


class _Meta:
    def __init__(self, id: str, name: str = "", description: str = "") -> None:
        self.id = id
        self.name = name
        self.description = description


class _Topo:
    def __init__(self, name: str, description: str) -> None:
        self.metadata = _Meta(name, name, description)


class _Raw:
    def __init__(self) -> None:
        self.metadata = _Meta("ws", "My workspace")


class _WS:
    def __init__(self) -> None:
        self.raw = _Raw()
        self.topologies = {
            "review": _Topo("review", "Reviews code."),
            "triage": _Topo("triage", ""),
        }


class _RT:
    workspace = _WS()


def test_card_has_one_skill_per_topology_and_no_scheme_without_auth() -> None:
    card = build_agent_card(_RT(), base_url="http://h:8000/", auth_provider="none")
    assert [s["id"] for s in card["skills"]] == ["review", "triage"]
    assert card["skills"][0]["description"] == "Reviews code."
    assert card["skills"][1]["description"] == "Run the triage topology."
    assert card["url"] == "http://h:8000/a2a"
    assert card["name"] == "My workspace"
    caps = card["capabilities"]
    assert caps["streaming"] is True
    assert caps["pushNotifications"] is False
    assert caps["stateTransitionHistory"] is False
    assert caps["extendedAgentCard"] is False
    # The SwarmKit A2A federation extension (a2a-federation.md) — a non-SwarmKit client ignores it.
    from swarmkit_runtime.server._a2a import SWARMKIT_A2A_EXTENSION  # noqa: PLC0415

    assert [e["uri"] for e in caps["extensions"]] == [SWARMKIT_A2A_EXTENSION]
    assert caps["extensions"][0]["params"]["returns_usage"] is True
    assert "securitySchemes" not in card
    assert "provider" not in card


@pytest.mark.parametrize("provider", ["api_key", "jwt"])
def test_card_advertises_bearer_when_serve_enforces_it(provider: str) -> None:
    card = build_agent_card(_RT(), base_url="http://h", auth_provider=provider)
    assert card["securitySchemes"] == {"bearer": {"type": "http", "scheme": "bearer"}}
    assert card["security"] == [{"bearer": []}]


def test_card_identity_overrides_name_url_and_provider() -> None:
    card = build_agent_card(
        _RT(),
        base_url="http://internal:8000",
        auth_provider="none",
        identity={
            "name": "Review desk",
            "description": "Gated review.",
            "url": "https://swarm.example.com/",
            "organization": "Example Org",
        },
    )
    assert card["name"] == "Review desk"
    assert card["description"] == "Gated review."
    assert card["url"] == "https://swarm.example.com/a2a"
    assert card["provider"] == {"organization": "Example Org", "url": "https://swarm.example.com"}


def test_per_topology_card_has_that_one_skill() -> None:
    card = build_agent_card(
        _RT(), base_url="http://h", auth_provider="none", only_topology="triage"
    )
    assert [s["id"] for s in card["skills"]] == ["triage"]
    assert card["url"] == "http://h/a2a/triage"


def test_server_cfg_defaults_a2a_off() -> None:
    assert ServerCfg().a2a_enabled is False
    assert ServerCfg().a2a_identity == {}


def test_parse_server_config_reads_a2a(a2a_workspace: Path) -> None:
    from swarmkit_runtime._workspace_runtime import WorkspaceRuntime  # noqa: PLC0415

    rt = WorkspaceRuntime.from_workspace_path(a2a_workspace)
    cfg = _parse_server_config(rt.workspace)
    assert cfg.a2a_enabled is True
    assert cfg.a2a_identity == {"name": "Hello desk", "organization": "Example Org"}


def test_well_known_path_is_auth_exempt_and_rpc_is_run_tier() -> None:
    assert _required_action("GET", WELL_KNOWN_PATH) is None
    assert _required_action("POST", "/a2a") == "run"
    assert _required_action("POST", "/a2a/hello") == "run"
    assert _required_action("GET", "/a2a/hello/card") == "read"


# ---- integration: disabled --------------------------------------------------------------------


def test_disabled_workspace_has_no_card_and_no_rpc(disabled_client: TestClient) -> None:
    assert disabled_client.get(WELL_KNOWN_PATH).status_code == 404
    assert disabled_client.post("/a2a", json={"jsonrpc": "2.0", "method": "x"}).status_code == 404
    caps = disabled_client.get("/capabilities").json()
    assert caps["features"]["a2a"] is False


# ---- integration: enabled ---------------------------------------------------------------------


def test_card_is_served_publicly(client: TestClient) -> None:
    resp = client.get(WELL_KNOWN_PATH)
    assert resp.status_code == 200
    card = resp.json()
    assert card["name"] == "Hello desk"
    assert [s["id"] for s in card["skills"]] == ["hello"]
    assert card["url"].endswith("/a2a")
    assert card["provider"]["organization"] == "Example Org"
    assert client.get("/capabilities").json()["features"]["a2a"] is True


def test_card_honours_forwarded_host(client: TestClient) -> None:
    resp = client.get(
        WELL_KNOWN_PATH,
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "swarm.example.com"},
    )
    assert resp.json()["url"] == "https://swarm.example.com/a2a"
    per = client.get("/a2a/hello/card").json()
    assert per["url"].endswith("/a2a/hello")
    assert client.get("/a2a/nope/card").status_code == 404


def test_send_message_runs_the_topology_as_a_job(client: TestClient) -> None:
    body = _send(client, "Greet engineers", contextId="ctx-1")
    task = body["result"]
    assert task["kind"] == "task"
    assert task["contextId"] == "ctx-1"
    assert task["status"]["state"] in ("submitted", "working")
    # The same job, seen through the job API — one execution path, not two.
    job = client.get(f"/jobs/{task['id']}").json()
    assert job["job_id"] == task["id"]
    done = _wait_terminal(client, task["id"])
    assert done["status"]["state"] == "completed"
    assert done["artifacts"], done
    history = client.get("/jobs/history?correlation_id=ctx-1").json()
    assert [j["job_id"] for j in history] == [task["id"]]
    assert history[0]["source"] == SOURCE


def test_send_via_per_topology_endpoint_needs_no_skill_metadata(client: TestClient) -> None:
    message = {
        "kind": "message",
        "role": "user",
        "messageId": "m",
        "parts": [{"kind": "text", "text": "hi"}],
    }
    body = _rpc(client, "message/send", {"message": message}, path="/a2a/hello")
    assert body["result"]["metadata"]["swarmkit"]["topology"] == "hello"
    assert (
        client.post("/a2a/nope", json={"jsonrpc": "2.0", "method": "tasks/get"}).status_code == 404
    )


def test_tasks_list_returns_only_a2a_runs(client: TestClient) -> None:
    client.post("/run/hello", json={"input": "over the job api"})
    body = _send(client, "over a2a", contextId="ctx-list")
    listed = _rpc(client, "tasks/list", {})["result"]["tasks"]
    ids = {t["id"] for t in listed}
    assert body["result"]["id"] in ids
    assert all(t["contextId"] for t in listed)
    narrowed = _rpc(client, "tasks/list", {"contextId": "ctx-list"})["result"]["tasks"]
    assert [t["id"] for t in narrowed] == [body["result"]["id"]]


def test_tasks_get_unknown_is_task_not_found(client: TestClient) -> None:
    body = _rpc(client, "tasks/get", {"id": "nope"})
    assert body["error"]["code"] == TASK_NOT_FOUND


def test_follow_up_on_a_task_is_refused(client: TestClient) -> None:
    body = _send(client, "first")
    task_id = body["result"]["id"]
    _wait_terminal(client, task_id)
    again = _send(client, "second", taskId=task_id)
    assert again["error"]["code"] == UNSUPPORTED_OPERATION
    assert "new message" in again["error"]["message"]


def test_cancel_terminal_task_is_not_cancelable(client: TestClient) -> None:
    body = _send(client, "to finish")
    _wait_terminal(client, body["result"]["id"])
    out = _rpc(client, "tasks/cancel", {"id": body["result"]["id"]})
    assert out["error"]["code"] == TASK_NOT_CANCELABLE


def test_json_rpc_errors(client: TestClient) -> None:
    resp = client.post("/a2a", content=b"{not json", headers={"content-type": "application/json"})
    assert resp.json()["error"]["code"] == PARSE_ERROR
    assert _rpc(client, "nope/x", {})["error"]["code"] == METHOD_NOT_FOUND
    assert (
        _rpc(client, "tasks/pushNotificationConfig/set", {})["error"]["code"]
        == PUSH_NOTIFICATION_NOT_SUPPORTED
    )
    assert _rpc(client, "message/send", {})["error"]["code"] == INVALID_PARAMS
    # A streaming method without the Accept header is a plain JSON refusal, not a hung socket.
    assert _rpc(client, "message/stream", {})["error"]["code"] == INVALID_REQUEST
    bad = client.post("/a2a", json={"id": 1, "method": "tasks/get"})
    assert bad.json()["error"]["code"] == INVALID_REQUEST


def test_message_stream_emits_status_updates_until_final(client: TestClient) -> None:
    message = {
        "kind": "message",
        "role": "user",
        "messageId": "m",
        "parts": [{"kind": "text", "text": "stream me"}],
        "metadata": {"skill": "hello"},
    }
    with client.stream(
        "POST",
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": "s",
            "method": "message/stream",
            "params": {"message": message},
        },
        headers={"accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = [
            json.loads(line[len("data: ") :])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]
    assert frames[0]["result"]["kind"] == "task"
    kinds = [f["result"]["kind"] for f in frames[1:]]
    assert "status-update" in kinds
    assert frames[-1]["result"]["final"] is True
    assert frames[-1]["result"]["status"]["state"] == "completed"
    assert any(k == "artifact-update" for k in kinds)


def test_tasks_subscribe_replays_a_finished_task(client: TestClient) -> None:
    task_id = _send(client, "then subscribe")["result"]["id"]
    _wait_terminal(client, task_id)
    with client.stream(
        "POST",
        "/a2a",
        json={"jsonrpc": "2.0", "id": "s", "method": "tasks/subscribe", "params": {"id": task_id}},
        headers={"accept": "text/event-stream"},
    ) as resp:
        frames = [
            json.loads(line[len("data: ") :])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]
    assert frames[-1]["result"]["final"] is True
    assert frames[-1]["result"]["taskId"] == task_id


# ---- the portal's client-side reads: probe a card, list remote agents ----------------------------


def test_probe_reads_a_remote_card_through_the_runtime(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The browser cannot fetch a cross-origin card; the runtime does, and reports what the agent
    offers. Here the "remote" is this very instance, reached through the test client's transport."""
    from swarmkit_runtime.agent_skill import _remote  # noqa: PLC0415

    real_client = _remote.A2AClient._client

    def _via_app(self: Any) -> Any:  # route the probe's httpx client into the app under test
        import httpx  # noqa: PLC0415

        return httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app))

    monkeypatch.setattr(_remote.A2AClient, "_client", _via_app)
    try:
        resp = client.get(
            "/api/a2a/probe", params={"card_url": "http://self/.well-known/agent-card.json"}
        )
    finally:
        monkeypatch.setattr(_remote.A2AClient, "_client", real_client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is True
    assert body["name"] == "Hello desk"
    assert [s["id"] for s in body["skills"]] == ["hello"]
    assert body["requires_bearer"] is False


def test_probe_reports_an_unreachable_card(client: TestClient) -> None:
    resp = client.get("/api/a2a/probe", params={"card_url": "http://127.0.0.1:9/nope"})
    body = resp.json()
    assert body["supported"] is False and "could not fetch" in body["detail"]


def test_remote_agents_lists_only_card_backed_agent_skills(
    tmp_path: Path, a2a_workspace: Path
) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    skill = """apiVersion: swarmkit/v1
kind: Skill
metadata: {id: %s, name: %s, description: Calls another agent for the test.}
category: capability
implementation:
%s
provenance: {authored_by: human, version: 1.0.0}
"""
    (a2a_workspace / "skills" / "legal.yaml").write_text(
        skill
        % (
            "legal",
            "Legal",
            "  type: agent\n  card_url: https://legal.example.com/card\n  skill_id: review\n"
            "  credentials_ref: legal\n  on_unanswerable: relay\n  permission: strict",
        )
    )
    (a2a_workspace / "skills" / "local.yaml").write_text(
        skill % ("local", "Local", "  type: agent\n  topology: hello")
    )
    with TestClient(create_app(a2a_workspace)) as c:
        rows = c.get("/api/a2a/agents").json()
    assert [r["id"] for r in rows] == ["legal"]
    assert rows[0]["card_url"] == "https://legal.example.com/card"
    assert rows[0]["skill_id"] == "review"
    assert rows[0]["credentials_ref"] == "legal"
    assert rows[0]["on_unanswerable"] == "relay"
    assert rows[0]["permission"] == "strict"
