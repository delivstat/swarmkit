"""An agent that calls a tool it does not hold names a capability gap, and the workspace records it.

The tool loop dropped such a call silently: no tool_result back to the model, no audit event, and
the skill gap log — the input to the growth cycle (design §12), read by `swarmkit gaps` — had no
writer anywhere in the runtime. Now the call answers with the tools the agent does have, the
audit gets a `skill.gap` event, and `swarmkit gaps` shows the row.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.gaps import SkillGapLog
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    Usage,
)

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: g, name: G}
governance: {provider: mock}
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
"""


@pytest.mark.asyncio
async def test_a_call_to_a_missing_tool_is_answered_and_recorded(
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
    first = CompletionResponse(
        content=(
            ContentBlock(
                type="tool_use",
                tool_name="translate-text",
                tool_input={"text": "hi", "to": "fr"},
                tool_use_id="t",
            ),
        ),
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    provider = MockModelProvider(responses={("m", "hi"): first})
    for key in list(rt._provider_registry._providers):
        rt._provider_registry._providers[key] = provider

    result = await rt.run("hello", "hi")
    assert result.output  # the run completed rather than hanging on a lost tool call
    gaps = [e for e in result.events if e.event_type == "skill.gap"]
    assert gaps and gaps[0].skill_id == "translate-text", [e.event_type for e in result.events]
    recorded = SkillGapLog(ws).list_gaps()
    assert [(g.skill_id, g.topology_id) for g in recorded] == [("translate-text", "hello")]
