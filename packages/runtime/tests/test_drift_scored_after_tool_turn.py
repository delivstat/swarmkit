"""`intent_monitoring` scores the answer an agent reached through a tool call.

Drift observation lived on the text-result path of the agent node only. A turn that called a
tool comes back through the tool loop as a dict and that branch returned first — so an agent
with `intent_monitoring: {enabled: true}` that did its work with tools recorded no
`intent.drift` event, no metric and never received a nudge. Same shape as the post_output gate
bypass fixed alongside it (`test_decision_bindings_actually_run`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    Usage,
)

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: drift, name: Drift}
governance:
  provider: mock
"""
_SKILL = """apiVersion: swarmkit/v1
kind: Skill
metadata: {id: note, name: Note, description: A prompt skill the agent can call in the test.}
category: capability
implementation: {type: llm_prompt, prompt: Echo.}
provenance: {authored_by: human, version: 1.0.0}
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: hello, version: 0.1.0}
agents:
  root:
    id: greeter
    role: root
    model: {provider: mock, name: m}
    prompt: {system: greet}
    skills: [note]
    intent_monitoring: {enabled: true, threshold: 0.75, on_drift: log}
"""


@pytest.mark.asyncio
async def test_drift_is_scored_when_the_agent_used_a_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(_WS)
    (ws / "skills" / "note.yaml").write_text(_SKILL)
    (ws / "topologies" / "hello.yaml").write_text(_TOPO)
    rt = WorkspaceRuntime.from_workspace_path(ws)
    call = CompletionResponse(
        content=(
            ContentBlock(
                type="tool_use", tool_name="note", tool_input={"input": "x"}, tool_use_id="t"
            ),
        ),
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    provider = MockModelProvider(responses={("m", "hi"): call})
    registry = rt._provider_registry
    for key in list(getattr(registry, "_providers", {})):
        registry._providers[key] = provider

    result = await rt.run("hello", "hi")
    drift = [e for e in result.events if e.event_type == "intent.drift"]
    # On the unfixed code this is [] — the dict-result branch returned before drift was observed.
    assert drift, [e.event_type for e in result.events]
    assert "drift_score" in drift[0].payload


def test_intent_monitoring_reaches_the_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The schema accepts `intent_monitoring` at topology and agent level; the resolver never
    copied either onto `ResolvedAgent`, so `_create_drift_observer` read `None` for every agent
    of every workspace and the feature had never run outside its unit tests."""
    from swarmkit_runtime.langgraph_compiler._drift import _create_drift_observer  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(_WS)
    (ws / "skills" / "note.yaml").write_text(_SKILL)
    (ws / "topologies" / "hello.yaml").write_text(_TOPO)
    (ws / "topologies" / "team.yaml").write_text(
        """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: team, version: 0.1.0}
intent_monitoring: {enabled: true, threshold: 0.9, on_drift: log}
agents:
  root:
    id: lead
    role: root
    model: {provider: mock, name: m}
    prompt: {system: lead}
    children:
      - id: strict
        role: worker
        model: {provider: mock, name: m}
        prompt: {system: w}
        intent_monitoring: {enabled: true, threshold: 0.5, on_drift: nudge}
      - id: inherits
        role: worker
        model: {provider: mock, name: m}
        prompt: {system: w}
"""
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    hello = rt._workspace.topologies["hello"].root
    assert _create_drift_observer(hello) is not None, hello.intent_monitoring
    team = rt._workspace.topologies["team"].root
    by_id = {c.id: c for c in team.children}
    assert _create_drift_observer(team).config.threshold == 0.9
    assert _create_drift_observer(by_id["strict"]).config.threshold == 0.5
    assert _create_drift_observer(by_id["inherits"]).config.threshold == 0.9
