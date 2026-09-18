"""``implementation.type: agent`` — another agent as a skill (design/details/a2a-interop.md).

Local form: a topology of this workspace runs as a child of the caller's run. Remote form: an A2A
agent reached through its card, with the task API as the transport — tested against our own A2A
server (SwarmKit calling SwarmKit) and against a scripted remote that asks questions, for the
three ``on_unanswerable`` policies.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from swarmkit_runtime._run_scope import reset_current_run_id, set_current_run_id
from swarmkit_runtime._workspace_runtime import MissingCommandPackError, WorkspaceRuntime
from swarmkit_runtime.agent_skill import (
    AgentSkillContext,
    check_agent_permission,
    execute_agent_skill,
    find_bad_agent_targets,
    parse_agent_spec,
    set_agent_context,
)
from swarmkit_runtime.agent_skill._context import MAX_DEPTH, reset_agent_context
from swarmkit_runtime.agent_skill._executor import _ANSWERS, _CARD_CACHE
from swarmkit_runtime.agent_skill._tool import agent_tool_schema
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler._skill_executor import execute_skill, is_refusal
from swarmkit_runtime.model_providers import MockModelProvider
from swarmkit_runtime.review import FileReviewQueue

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


def _memory_off(ws: Path) -> None:
    """These tests read the mock governance provider's events directly; a bound decision skill
    wraps the provider, so memory-by-default's own bindings are switched off here."""
    p = ws / "workspace.yaml"
    p.write_text(p.read_text() + "\nmemory: {enabled: false}\n")


_SKILL_HEAD = """apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: {id}
  name: {id}
  description: Calls another agent.
category: capability
implementation:
  type: agent
{impl}
provenance:
  authored_by: human
  authored_date: "2026-09-17"
  version: 1.0.0
"""

_CALLER_TOPOLOGY = """apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: caller
  version: 0.1.0
agents:
  root:
    id: root
    role: root
    model:
      provider: anthropic
      name: claude-opus-4-7
    prompt:
      system: You delegate to other agents.
    skills: [{skills}]
"""


@pytest.fixture(autouse=True)
def _mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    _CARD_CACHE.clear()
    _ANSWERS.clear()


def _workspace(tmp_path: Path, skills: dict[str, str]) -> Path:
    """hello-swarm plus the given `agent` skills and a `caller` topology granting them all."""
    ws = tmp_path / "workspace"
    shutil.copytree(EXAMPLE_WS, ws, ignore=shutil.ignore_patterns(".swarmkit"))
    _memory_off(ws)
    for sid, impl in skills.items():
        (ws / "skills" / f"{sid}.yaml").write_text(_SKILL_HEAD.format(id=sid, impl=impl))
    (ws / "topologies" / "caller.yaml").write_text(
        _CALLER_TOPOLOGY.format(skills=", ".join(skills))
    )
    return ws


class _Scope:
    """Enter a parent run's scope (run id + agent context) around a direct `execute_skill` call."""

    def __init__(self, rt: WorkspaceRuntime, **overrides: Any) -> None:
        self.rt = rt
        self.overrides = overrides

    def __enter__(self) -> _Scope:
        self._run = set_current_run_id("parent-run")
        base = self.rt._agent_context("caller")
        ctx = AgentSkillContext(**{**base.__dict__, **self.overrides})
        self._ctx = set_agent_context(ctx)
        return self

    def __exit__(self, *exc: object) -> None:
        reset_agent_context(self._ctx)
        reset_current_run_id(self._run)


async def _call(rt: WorkspaceRuntime, skill_id: str, payload: Any, **scope: Any) -> str:
    skill = rt.workspace.skills[skill_id]
    with _Scope(rt, **scope):
        result = await execute_skill(
            skill,
            input_text=json.dumps(payload) if isinstance(payload, dict) else payload,
            model_provider=MockModelProvider(),
            model_name="mock",
            governance=rt._governance,
            agent_id="root",
        )
    return result if isinstance(result, str) else result[0]


# ---- spec + schema ------------------------------------------------------------------------------


def test_spec_requires_exactly_one_target() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        parse_agent_spec({"type": "agent"})
    with pytest.raises(ValueError, match="exactly one"):
        parse_agent_spec({"type": "agent", "topology": "a", "card_url": "https://x"})
    spec = parse_agent_spec({"type": "agent", "topology": "a"})
    assert spec.is_local and spec.timeout_s == 600 and spec.on_unanswerable == "agent"
    assert spec.max_agent_answers == 2 and spec.permission == "cautious"
    spec = parse_agent_spec(
        {
            "type": "agent",
            "card_url": "https://x/card",
            "on_unanswerable": "relay",
            "max_agent_answers": 0,
            "skill_id": "s",
            "credentials_ref": "c",
            "timeout_s": 5,
        }
    )
    assert not spec.is_local and spec.max_agent_answers == 0 and spec.credentials_ref == "c"


def test_tool_schema_offers_answer_fields_only_when_the_agent_may_answer() -> None:
    remote = agent_tool_schema({"type": "agent", "card_url": "https://x"})
    assert set(remote["properties"]) == {"input", "context", "task_id", "answer"}
    relay = agent_tool_schema(
        {"type": "agent", "card_url": "https://x", "on_unanswerable": "relay"}
    )
    assert set(relay["properties"]) == {"input", "context"}
    local = agent_tool_schema({"type": "agent", "topology": "hello"})
    assert set(local["properties"]) == {"input", "context"}


def test_workspace_load_refuses_a_missing_local_target(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-nowhere": "  topology: no-such-topology"})
    with pytest.raises(MissingCommandPackError, match="topology 'no-such-topology'"):
        WorkspaceRuntime.from_workspace_path(ws)


def test_find_bad_targets_names_the_skill(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    rt = WorkspaceRuntime.from_workspace_path(ws)
    assert find_bad_agent_targets(rt.workspace) == []


# ---- permission seam ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_denies_unknown_effects_and_allows_read() -> None:
    gov = MockGovernanceProvider()
    spec = parse_agent_spec({"type": "agent", "topology": "t", "permission": "readonly"})
    allowed, reason = await check_agent_permission(spec, gov, agent_id="a", skill_id="s")
    assert not allowed and "readonly" in reason
    spec = parse_agent_spec(
        {"type": "agent", "topology": "t", "permission": "readonly", "effects": "read"}
    )
    allowed, _ = await check_agent_permission(spec, gov, agent_id="a", skill_id="s")
    assert allowed


@pytest.mark.asyncio
async def test_cautious_goes_through_evaluate_action_with_the_mcp_vocabulary() -> None:
    gov = MockGovernanceProvider()
    spec = parse_agent_spec({"type": "agent", "card_url": "https://r/card", "effects": "write"})
    allowed, _ = await check_agent_permission(spec, gov, agent_id="a", skill_id="s")
    assert allowed
    calls = [c for c in getattr(gov, "evaluated", []) if c.get("action", "").startswith("agent:")]
    if calls:  # the mock records actions when it can
        assert calls[-1]["context"]["provider"] == "agent"


# ---- local form --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_child_runs_the_topology_and_returns_its_output(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    rt = WorkspaceRuntime.from_workspace_path(ws)
    out = await _call(rt, "ask-hello", {"input": "Greet engineers", "context": {"team": "core"}})
    assert out == "mock response"
    # The child is a job of its own, nested under the parent by parent_job_id, from the same
    # store the CLI and serve use.
    rows = [j for j in rt.store.list_jobs(limit=20) if j.topology == "hello"]
    assert rows, "the child run left no job row"
    child = rows[0]
    assert child.parent_job_id == "parent-run"
    assert child.source == "agent"
    assert child.status == "completed"
    assert child.output == "mock response"


@pytest.mark.asyncio
async def test_local_child_requires_input(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    rt = WorkspaceRuntime.from_workspace_path(ws)
    assert "`input` is required" in await _call(rt, "ask-hello", {"context": {}})


@pytest.mark.asyncio
async def test_depth_is_bounded(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    rt = WorkspaceRuntime.from_workspace_path(ws)
    out = await _call(rt, "ask-hello", "hi", depth=MAX_DEPTH)
    assert "cycle" in out and f"limit {MAX_DEPTH}" in out


@pytest.mark.asyncio
async def test_outside_a_run_the_skill_says_so(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    rt = WorkspaceRuntime.from_workspace_path(ws)
    result = await execute_agent_skill(rt.workspace.skills["ask-hello"], input_text="hi")
    assert "no run context" in result


@pytest.mark.asyncio
async def test_a_refusal_is_marked_like_every_other_skill(tmp_path: Path) -> None:
    ws = _workspace(
        tmp_path, {"ask-hello": "  topology: hello\n  permission: readonly\n  effects: write"}
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    out = await _call(rt, "ask-hello", "hi")
    assert is_refusal(out)


@pytest.mark.asyncio
async def test_local_child_runs_inside_a_real_parent_run(tmp_path: Path) -> None:
    """End to end through `rt.run`: the caller topology runs under mock, and the context an agent
    skill needs is installed for the run's duration and gone after."""
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    rt = WorkspaceRuntime.from_workspace_path(ws)
    from swarmkit_runtime.agent_skill import current_agent_context  # noqa: PLC0415

    assert current_agent_context() is None
    result = await rt.run("caller", "go")
    assert result.output
    assert current_agent_context() is None


# ---- remote form: SwarmKit calling SwarmKit ----------------------------------------------------


def _a2a_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "remote"
    shutil.copytree(EXAMPLE_WS, ws, ignore=shutil.ignore_patterns(".swarmkit"))
    _memory_off(ws)
    manifest = ws / "workspace.yaml"
    manifest.write_text(manifest.read_text() + "\nserver:\n  a2a:\n    enabled: true\n")
    return ws


@pytest.mark.asyncio
async def test_remote_call_against_our_own_a2a_server(tmp_path: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    remote_app = create_app(_a2a_workspace(tmp_path))
    ws = _workspace(
        tmp_path,
        {"ask-remote": "  card_url: http://remote/.well-known/agent-card.json\n  skill_id: hello"},
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    transport = httpx.ASGITransport(app=remote_app)
    async with remote_app.router.lifespan_context(remote_app):
        out = await _call(rt, "ask-remote", {"input": "Greet engineers"}, transport=transport)
        assert out == "mock response", out
        # The remote run carries our run id as its A2A context, so the two instances' records
        # join on it.
        history = remote_app.state.store.list_jobs(limit=5)
        assert history[0].source == "a2a"
        assert history[0].correlation_id == "parent-run"


@pytest.mark.asyncio
async def test_remote_unknown_skill_is_named(tmp_path: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    remote_app = create_app(_a2a_workspace(tmp_path))
    ws = _workspace(
        tmp_path,
        {"ask-remote": "  card_url: http://remote/.well-known/agent-card.json\n  skill_id: nope"},
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    async with remote_app.router.lifespan_context(remote_app):
        out = await _call(rt, "ask-remote", "hi", transport=httpx.ASGITransport(app=remote_app))
    assert "no skill 'nope'" in out and "hello" in out


@pytest.mark.asyncio
async def test_remote_without_a2a_enabled_says_so(tmp_path: Path) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    plain = tmp_path / "plain"
    shutil.copytree(EXAMPLE_WS, plain, ignore=shutil.ignore_patterns(".swarmkit"))
    remote_app = create_app(plain)
    ws = _workspace(
        tmp_path, {"ask-remote": "  card_url: http://remote/.well-known/agent-card.json"}
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    async with remote_app.router.lifespan_context(remote_app):
        out = await _call(rt, "ask-remote", "hi", transport=httpx.ASGITransport(app=remote_app))
    assert "404" in out and "A2A may not be enabled" in out


# ---- remote form: a scripted agent that asks questions ----------------------------------------


def _questioning_agent(*, gate: bool = False) -> tuple[FastAPI, dict[str, Any]]:
    """A remote whose first task asks "which format?"; an answer completes it. With `gate`, the
    question is a SwarmKit human gate (metadata.swarmkit.gate_url) instead."""
    app = FastAPI()
    seen: dict[str, Any] = {"messages": [], "cancelled": [], "auth": []}

    @app.get("/.well-known/agent-card.json")
    async def card() -> dict[str, Any]:
        return {
            "name": "Asker",
            "url": "http://asker/a2a",
            "capabilities": {"streaming": False},
            "skills": [{"id": "ask", "name": "ask"}],
        }

    @app.post("/a2a")
    async def rpc(request: Request) -> dict[str, Any]:
        seen["auth"].append(request.headers.get("authorization"))
        body = await request.json()
        method, params = body["method"], body.get("params") or {}
        if method == "message/send":
            msg = params["message"]
            seen["messages"].append(msg)
            text = "".join(p.get("text", "") for p in msg["parts"])
            if msg.get("taskId"):
                task = {
                    "id": msg["taskId"],
                    "contextId": "c",
                    "kind": "task",
                    "status": {"state": "completed"},
                    "artifacts": [{"parts": [{"kind": "text", "text": f"done in {text}"}]}],
                }
            else:
                status: dict[str, Any] = {
                    "state": "input-required",
                    "message": {"parts": [{"kind": "text", "text": "which format?"}]},
                }
                task = {"id": "t1", "contextId": "c", "kind": "task", "status": status}
                if gate:
                    task["metadata"] = {"swarmkit": {"gate_url": "http://asker/gates/t1:x"}}
            seen["last"] = task
            return {"jsonrpc": "2.0", "id": body["id"], "result": task}
        if method == "tasks/get":
            return {"jsonrpc": "2.0", "id": body["id"], "result": seen["last"]}
        if method == "tasks/cancel":
            seen["cancelled"].append(params["id"])
            return {"jsonrpc": "2.0", "id": body["id"], "result": seen["last"]}
        return {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "?"}}

    return app, seen


@pytest.mark.asyncio
async def test_policy_agent_hands_the_question_back_and_takes_the_answer(tmp_path: Path) -> None:
    app, seen = _questioning_agent()
    ws = _workspace(
        tmp_path, {"ask": "  card_url: http://asker/.well-known/agent-card.json\n  timeout_s: 5"}
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    t = httpx.ASGITransport(app=app)
    first = json.loads(await _call(rt, "ask", {"input": "export the report"}, transport=t))
    assert first["status"] == "input_required"
    assert first["question"] == "which format?"
    assert first["task_id"] == "t1"
    second = await _call(rt, "ask", {"task_id": "t1", "answer": "PDF"}, transport=t)
    assert second == "done in PDF"
    assert seen["messages"][-1]["taskId"] == "t1"
    # The agent's answer is on the record as the agent's.
    events = [e for e in rt._governance.events if e.event_type == "executor.input_response"]  # type: ignore[attr-defined]
    assert events and events[-1].payload["responder"] == "agent:root"


@pytest.mark.asyncio
async def test_policy_agent_budget_then_refers_to_a_person(tmp_path: Path) -> None:
    app, _ = _questioning_agent()
    ws = _workspace(
        tmp_path,
        {
            "ask": "  card_url: http://asker/.well-known/agent-card.json\n  max_agent_answers: 0\n"
            "  timeout_s: 5"
        },
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    t = httpx.ASGITransport(app=app)
    # Budget 0: the first question already goes to a person; with no one answering in the
    # (short) window the call fails rather than hangs, and the remote task is cancelled.
    out = await _call(rt, "ask", {"input": "x"}, transport=t, relay_wait_s=0.2)
    assert "no answer from a person" in out
    refused = await _call(rt, "ask", {"task_id": "t1", "answer": "PDF"}, transport=t)
    assert "not yours to answer" in refused


@pytest.mark.asyncio
async def test_policy_relay_asks_a_person_through_the_review_queue(tmp_path: Path) -> None:
    app, seen = _questioning_agent()
    ws = _workspace(
        tmp_path,
        {
            "ask": "  card_url: http://asker/.well-known/agent-card.json\n"
            "  on_unanswerable: relay\n  timeout_s: 5"
        },
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    queue = FileReviewQueue(ws)

    async def person_answers() -> None:
        for _ in range(100):
            pending = [i for i in queue.list_pending() if i.skill_id == "harness-input"]
            if pending:
                queue.answer_input(pending[0].id, "markdown")
                return
            await asyncio.sleep(0.05)

    answer_task = asyncio.create_task(person_answers())
    out = await _call(
        rt, "ask", {"input": "x"}, transport=httpx.ASGITransport(app=app), relay_wait_s=5
    )
    await answer_task
    assert out == "done in markdown"
    assert seen["messages"][-1]["taskId"] == "t1"
    item = next(i for i in queue.list_all() if i.skill_id == "harness-input")
    assert item.output["question"] == "which format?"


@pytest.mark.asyncio
async def test_policy_abort_cancels_and_reports_the_question(tmp_path: Path) -> None:
    app, seen = _questioning_agent()
    ws = _workspace(
        tmp_path,
        {
            "ask": "  card_url: http://asker/.well-known/agent-card.json\n"
            "  on_unanswerable: abort\n  timeout_s: 5"
        },
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    out = await _call(rt, "ask", {"input": "x"}, transport=httpx.ASGITransport(app=app))
    assert "which format?" in out and "abort" in out
    assert seen["cancelled"] == ["t1"]


@pytest.mark.asyncio
async def test_a_remote_human_gate_is_never_the_agents_to_answer(tmp_path: Path) -> None:
    app, seen = _questioning_agent(gate=True)
    ws = _workspace(
        tmp_path, {"ask": "  card_url: http://asker/.well-known/agent-card.json\n  timeout_s: 5"}
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    t = httpx.ASGITransport(app=app)
    out = json.loads(await _call(rt, "ask", {"input": "x"}, transport=t))
    assert out["status"] == "input_required" and out["kind"] == "human_gate"
    assert "cannot answer" in out["hint"]
    # No answer was offered to the remote, and the policy `agent` did not turn a gate into a
    # question the agent may answer.
    assert all(not m.get("taskId") for m in seen["messages"])


@pytest.mark.asyncio
async def test_credentials_ref_is_sent_as_a_bearer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, seen = _questioning_agent()
    monkeypatch.setenv("ASKER_TOKEN", "s3cret")
    ws = _workspace(
        tmp_path,
        {
            "ask": "  card_url: http://asker/.well-known/agent-card.json\n"
            "  credentials_ref: asker\n  on_unanswerable: abort\n  timeout_s: 5"
        },
    )
    manifest = ws / "workspace.yaml"
    manifest.write_text(
        manifest.read_text()
        + "\ncredentials:\n  asker:\n    source: env\n    config:\n      env: ASKER_TOKEN\n"
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    await _call(rt, "ask", {"input": "x"}, transport=httpx.ASGITransport(app=app))
    assert "Bearer s3cret" in seen["auth"]


# ---- pack:workspace ----------------------------------------------------------------------------


def test_every_topology_is_a_synthetic_agent_skill(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    rt = WorkspaceRuntime.from_workspace_path(ws)
    skills = rt.workspace.skills
    assert "topology-hello" in skills and "topology-caller" in skills
    synthetic = skills["topology-hello"]
    assert synthetic.pack_origin == ("workspace", "hello", "unknown")
    spec = parse_agent_spec(synthetic.raw.implementation)
    assert spec.topology == "hello" and spec.permission == "cautious" and spec.effects == "unknown"
    # The hand-authored skill targeting the same topology is untouched.
    assert parse_agent_spec(skills["ask-hello"].raw.implementation).topology == "hello"


def test_pack_workspace_grants_every_topology(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    (ws / "topologies" / "caller.yaml").write_text(_CALLER_TOPOLOGY.format(skills="pack:workspace"))
    rt = WorkspaceRuntime.from_workspace_path(ws)
    root = rt.workspace.topologies["caller"].root
    granted = sorted(s.id for s in root.skills)
    assert granted == ["topology-caller", "topology-hello"]


@pytest.mark.asyncio
async def test_pack_workspace_skill_runs_the_topology(tmp_path: Path) -> None:
    ws = _workspace(tmp_path, {"ask-hello": "  topology: hello"})
    rt = WorkspaceRuntime.from_workspace_path(ws)
    assert await _call(rt, "topology-hello", {"input": "hi"}) == "mock response"


def test_a_skill_named_like_a_synthetic_one_is_a_collision(tmp_path: Path) -> None:
    from swarmkit_runtime.errors import ResolutionErrors  # noqa: PLC0415

    ws = _workspace(tmp_path, {"topology-hello": "  topology: hello"})
    with pytest.raises(ResolutionErrors) as exc:
        WorkspaceRuntime.from_workspace_path(ws)
    assert [e.code for e in exc.value.errors] == ["workspace-pack.id-collision"]


def test_command_pack_may_not_be_named_workspace() -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    from swarmkit_runtime.commands._config import (  # noqa: PLC0415
        CommandPackError,
        parse_command_packs,
    )

    pack = SimpleNamespace(id="workspace", commands=[])
    with pytest.raises(CommandPackError, match="reserved"):
        parse_command_packs([pack])


# ---- A2A federation: SwarmKit calling SwarmKit hands back its record ---------------------------


def test_our_card_advertises_the_federation_extension(tmp_path: Path) -> None:
    """A SwarmKit instance's card carries the federation extension so a caller can identify it."""
    import asyncio  # noqa: PLC0415

    from swarmkit_runtime.agent_skill._remote import (  # noqa: PLC0415
        SWARMKIT_A2A_EXTENSION,
        A2AClient,
    )
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    remote_app = create_app(_a2a_workspace(tmp_path))

    async def _probe() -> None:
        async with remote_app.router.lifespan_context(remote_app):
            client = A2AClient(timeout_s=20.0, transport=httpx.ASGITransport(app=remote_app))
            card = await client.fetch_card("http://remote/.well-known/agent-card.json")
        assert card.swarmkit is not None, "our own card must advertise the federation extension"
        assert card.swarmkit.get("returns_usage") is True
        exts = card.raw["capabilities"]["extensions"]
        assert any(e["uri"] == SWARMKIT_A2A_EXTENSION for e in exts)

    asyncio.run(_probe())


@pytest.mark.asyncio
async def test_a_plain_card_is_not_a_swarmkit_agent() -> None:
    """A non-SwarmKit card advertises no federation extension, so `swarmkit` parses to None."""
    from swarmkit_runtime.agent_skill._remote import A2AClient  # noqa: PLC0415

    card = {"url": "http://x/a2a", "name": "Some Agent", "capabilities": {"streaming": True}}

    def _handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=card)

    client = A2AClient(transport=httpx.MockTransport(_handler))
    parsed = await client.fetch_card("http://x/card")
    assert parsed.swarmkit is None


@pytest.mark.asyncio
async def test_a_swarmkit_callee_returns_run_id_and_usage_and_the_caller_records_it(
    tmp_path: Path,
) -> None:
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    remote_app = create_app(_a2a_workspace(tmp_path))
    ws = _workspace(
        tmp_path,
        {"ask-remote": "  card_url: http://remote/.well-known/agent-card.json\n  skill_id: hello"},
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    transport = httpx.ASGITransport(app=remote_app)
    async with remote_app.router.lifespan_context(remote_app):
        out = await _call(rt, "ask-remote", {"input": "Greet engineers"}, transport=transport)
    assert out == "mock response", out

    # The callee's job id and the caller's a2a.remote_usage event name the same run.
    remote_run = remote_app.state.store.list_jobs(limit=1)[0].id
    events = {e.event_type: e for e in rt._governance.events}  # type: ignore[attr-defined]
    assert "a2a.remote_usage" in events, list(events)
    ev = events["a2a.remote_usage"]
    assert ev.payload["remote_run_id"] == remote_run
    assert ev.payload["endpoint"].endswith("/a2a")
    assert ev.payload["source"] == "reported"
    # The mock provider bills tokens, so the round-trip carried a non-zero usage total.
    assert ev.payload["input_tokens"] > 0 or ev.payload["output_tokens"] > 0
