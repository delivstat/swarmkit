"""The `memory-writer` binding runs on an answer the agent reached through a tool call.

The writer (and the governed-memory candidate write) lived on the text-result path of the agent
node only; a turn that called a tool returned through the loop as a dict before them. So a
workspace with `memory-writer` bound remembered only the turns in which the agent used no tool —
a trip-planning answer that called `get-weather` three times stored nothing, and the next run's
`memory-reader` found nothing. Fourth feature in the same trap, after the
post_output gate, drift scoring and the ring buffer; the four now share one code path.
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
metadata: {id: mem, name: Mem}
governance:
  provider: mock
  decision_skills:
    - {id: memory-writer, trigger: post_output, scope: "*", required: false,
       config: {min_output_length: 1}}
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
async def test_memory_is_written_after_a_tool_turn(
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
    extraction = CompletionResponse(
        content=(
            ContentBlock(
                type="text",
                text='{"worth_saving": true, "topic": "trip", "context": "Japan in November",'
                ' "key_points": ["pack layers"], "tags": ["travel"]}',
            ),
        ),
        stop_reason="end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )
    # The first turn calls the tool; every later call (the loop's synthesis, the extraction
    # prompt) gets the extraction JSON — which is also a fine final answer for the test.
    provider = MockModelProvider(responses={("m", "hi"): call}, default_response=extraction)
    registry = rt._provider_registry
    for key in list(getattr(registry, "_providers", {})):
        registry._providers[key] = provider

    await rt.run("hello", "hi")
    # On the unfixed code nothing was stored: the dict branch returned before the writer.
    from swarmkit_runtime.memory import MemoryStore  # noqa: PLC0415

    entries = MemoryStore(ws).list_all()
    assert [e.context for e in entries] == ["Japan in November"]
