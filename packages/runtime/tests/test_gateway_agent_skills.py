"""An `agent` skill offered to a harness through the governed gateway (a2a-interop.md).

The gateway flattens it as `agent__<skill>`; a call runs the skill's executor under the run scope
captured at registration — so a harness node delegating to another topology gets a child run that
is attributed to the parent, correlated, and depth-bounded, exactly as a model node would.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from swarmkit_runtime._run_scope import reset_current_run_id, set_current_run_id
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.agent_skill import set_agent_context
from swarmkit_runtime.agent_skill._context import reset_agent_context
from swarmkit_runtime.mcp._gateway import (
    AGENT_SERVER_ID,
    build_agent_gateway_tools,
    mcp_gateway,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"

_SKILL = """apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: {id}
  name: {id}
  description: Calls another agent for the test.
category: capability
implementation:
  type: agent
{impl}
provenance:
  authored_by: human
  authored_date: "2026-09-17"
  version: 1.0.0
"""


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")


def _rt(tmp_path: Path, impl: str) -> WorkspaceRuntime:
    ws = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, ws, ignore=shutil.ignore_patterns(".swarmkit"))
    (ws / "skills" / "ask-hello.yaml").write_text(_SKILL.format(id="ask-hello", impl=impl))
    return WorkspaceRuntime.from_workspace_path(ws)


def test_agent_gateway_tools_are_flat_and_carry_the_tool_schema(tmp_path: Path) -> None:
    rt = _rt(tmp_path, "  topology: hello")
    tools = build_agent_gateway_tools([rt.workspace.skills["ask-hello"]])
    assert [t.name for t in tools] == [f"{AGENT_SERVER_ID}__ask-hello"]
    tool = tools[0]
    assert tool.server_id == AGENT_SERVER_ID and tool.tool_name == "ask-hello"
    assert tool.skill_id == "ask-hello" and tool.agent_skill is not None
    assert set(tool.input_schema["properties"]) == {"input", "context"}


@pytest.mark.asyncio
async def test_harness_call_runs_a_child_of_the_registering_run(tmp_path: Path) -> None:
    """Over real SSE, as a harness would call it. The run scope is set when the gateway is
    registered and absent on the serving task; the child run must still land under the parent."""
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.sse import sse_client  # noqa: PLC0415

    rt = _rt(tmp_path, "  topology: hello")
    tools = build_agent_gateway_tools([rt.workspace.skills["ask-hello"]])
    run_token = set_current_run_id("harness-parent")
    ctx_token = set_agent_context(rt._agent_context("caller"))
    try:
        async with mcp_gateway(tools, None, rt._governance, agent_id="coder") as gw:
            headers = {"Authorization": f"Bearer {gw.token}"}
            async with (
                sse_client(gw.url, headers=headers) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                listed = await session.list_tools()
                assert [t.name for t in listed.tools] == ["agent__ask-hello"]
                result = await session.call_tool("agent__ask-hello", {"input": "greet"})
                text = result.content[0].text  # type: ignore[union-attr]
    finally:
        reset_agent_context(ctx_token)
        reset_current_run_id(run_token)
    assert text == "mock response"
    child = next(j for j in rt.store.list_jobs(limit=10) if j.topology == "hello")
    assert child.parent_job_id == "harness-parent"
    assert child.source == "agent"
    assert gw.called == 1
    # Audited as the harness's own skill call, under the parent run.
    events = [e for e in rt._governance.events if e.event_type == "skill.executed"]  # type: ignore[attr-defined]
    mine = [e for e in events if e.skill_id == "agent__ask-hello"]
    assert mine and mine[-1].run_id == "harness-parent"
    assert mine[-1].policy_decision == "allow"


@pytest.mark.asyncio
async def test_harness_call_refused_under_readonly_is_audited_as_a_denial(
    tmp_path: Path,
) -> None:
    from mcp import ClientSession  # noqa: PLC0415
    from mcp.client.sse import sse_client  # noqa: PLC0415

    rt = _rt(tmp_path, "  topology: hello\n  permission: readonly\n  effects: write")
    tools = build_agent_gateway_tools([rt.workspace.skills["ask-hello"]])
    run_token = set_current_run_id("harness-parent")
    ctx_token = set_agent_context(rt._agent_context("caller"))
    try:
        async with mcp_gateway(tools, None, rt._governance, agent_id="coder") as gw:
            headers = {"Authorization": f"Bearer {gw.token}"}
            async with (
                sse_client(gw.url, headers=headers) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool("agent__ask-hello", {"input": "greet"})
                text = result.content[0].text  # type: ignore[union-attr]
    finally:
        reset_agent_context(ctx_token)
        reset_current_run_id(run_token)
    assert "DENIED" in text and "readonly" in text
    events = [e for e in rt._governance.events if e.skill_id == "agent__ask-hello"]  # type: ignore[attr-defined]
    assert events[-1].policy_decision == "deny"
    assert not [j for j in rt.store.list_jobs(limit=10) if j.topology == "hello"]


def test_harness_task_spec_grants_agent_tools(tmp_path: Path) -> None:
    from swarmkit_runtime.langgraph_compiler._harness_node import (  # noqa: PLC0415
        _granted_agent_skills,
    )

    ws = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, ws, ignore=shutil.ignore_patterns(".swarmkit"))
    (ws / "skills" / "ask-hello.yaml").write_text(
        _SKILL.format(id="ask-hello", impl="  topology: hello")
    )
    (ws / "topologies" / "caller.yaml").write_text(
        """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: caller, version: 0.1.0}
agents:
  root:
    id: root
    role: root
    model: {provider: anthropic, name: claude-opus-4-7}
    prompt: {system: delegate}
    skills: [ask-hello, say-hello]
"""
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    root = rt.workspace.topologies["caller"].root
    assert [s.id for s in _granted_agent_skills(root)] == ["ask-hello"]
