"""Calling the `governed-memory` skill writes through the governed path.

The skill is offered to an agent as a tool. Calling it ran its prompt and handed the resulting
`{"memories": [...]}` back as a tool result — which nothing read: the only writer was the
post_output hook over the agent's final prose. An agent that called the tool and then answered
"Got it, noted" had written nothing, and said it had. The call now writes, and its result reports
each candidate's reconcile op so the agent can tell the user what happened.
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

REPO = Path(__file__).resolve().parents[3]

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: mem, name: Mem}
governance: {provider: mock}
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: tutor, version: 0.1.0}
agents:
  root:
    id: tutor
    role: root
    model: {provider: mock, name: m}
    prompt: {system: remember things}
    skills: [governed-memory]
"""


@pytest.mark.asyncio
async def test_the_skill_call_itself_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(_WS)
    (ws / "skills" / "governed-memory.yaml").write_text(
        (REPO / "reference/skills/governed-memory.yaml").read_text()
    )
    (ws / "topologies" / "tutor.yaml").write_text(_TOPO)
    rt = WorkspaceRuntime.from_workspace_path(ws)

    call = CompletionResponse(
        content=(
            ContentBlock(
                type="tool_use",
                tool_name="governed-memory",
                tool_input={"input": "user is vegetarian"},
                tool_use_id="t",
            ),
        ),
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    candidates = CompletionResponse(
        content=(
            ContentBlock(
                type="text",
                text='{"memories": [{"subject": "user:alice", "attribute": "diet",'
                ' "value": "vegetarian", "type": "profile"}]}',
            ),
        ),
        stop_reason="end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    prose = CompletionResponse(
        content=(ContentBlock(type="text", text="Got it, noted."),),
        stop_reason="end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    # First turn calls the tool; the skill's own prompt gets the candidates; the loop's synthesis
    # answers in prose (no JSON in the final output — the post_output hook has nothing to parse).
    provider = MockModelProvider(
        # A single `input` argument reaches the skill's prompt as the plain text.
        responses={("m", "remember me"): call, ("m", "user is vegetarian"): candidates},
        default_response=prose,
    )
    registry = rt._provider_registry
    for key in list(getattr(registry, "_providers", {})):
        registry._providers[key] = provider

    result = await rt.run("tutor", "remember me")
    assert result.output == "Got it, noted."
    # On the unfixed code the store was empty: the candidates JSON was a tool result nobody read.
    found = rt.governed_memory.get("user:alice", "diet")
    assert found is not None and found.value == "vegetarian"
    assert found.source == "tutor"
