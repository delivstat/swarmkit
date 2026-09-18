"""A `governance.decision_skills` binding runs its skill — the skill-backed provider is built.

`WorkspaceRuntime.from_workspace_path` wraps governance in `SkillBackedGovernanceProvider` only
when the workspace has decision skills, and it found them with
`skill.raw.category == "decision"`. `Category` generated as a plain `Enum`, so that was False for
every skill loaded from YAML: the wrapper was never built, `evaluate_decision_skill` fell to the
mock's canned pass, and no bound decision skill had ever executed from a workspace — a content
filter bound `pre_input` blocked nothing. (#781 fixed the same trap for `Trigger` and said it
covered every enum; the codegen flag only covers enums whose schema also says `type: string`,
which most do not. The codegen now rewrites every string enum to `StrEnum`.)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance._skill_backed import SkillBackedGovernanceProvider

_WS_POST = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: gated, name: Gated}
memory: {enabled: false}   # these tests assert the exact bindings that ran
governance:
  provider: mock
  decision_skills:
    - {id: content-filter, trigger: post_output, scope: "*"}
"""
_TOOL_SKILL = """apiVersion: swarmkit/v1
kind: Skill
metadata: {id: note, name: Note, description: A prompt skill the agent can call in the test.}
category: capability
implementation: {type: llm_prompt, prompt: Echo.}
provenance: {authored_by: human, version: 1.0.0}
"""
_TOPO_TOOL = """apiVersion: swarmkit/v1
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

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: gated, name: Gated}
memory: {enabled: false}
governance:
  provider: mock
  decision_skills:
    - {id: content-filter, trigger: pre_input, scope: "*"}
"""
_SKILL = """apiVersion: swarmkit/v1
kind: Skill
metadata: {id: content-filter, name: Filter, description: Fails everything for the test.}
category: decision
implementation:
  type: llm_prompt
  prompt: 'Reply with JSON {"verdict": "fail", "reasoning": "test filter"}.'
outputs:
  type: object
  required: [verdict, reasoning]
  properties:
    verdict: {type: string, enum: [pass, fail]}
    reasoning: {type: string}
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
"""


def test_category_is_a_string_now() -> None:
    from swarmkit_schema.models.skill import Category  # noqa: PLC0415

    assert Category.decision == "decision"


def test_workspace_with_decision_skills_gets_the_skill_backed_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(_WS)
    (ws / "skills" / "content-filter.yaml").write_text(_SKILL)
    (ws / "topologies" / "hello.yaml").write_text(_TOPO)
    rt = WorkspaceRuntime.from_workspace_path(ws)
    # On the unfixed code this was the bare MockGovernanceProvider: the binding named a skill the
    # provider could not run, and answered "pass" for it without running anything. The write-through
    # audit journal (audit-event-journal.md) now wraps whatever provider the runtime built, so the
    # skill-backed provider is one `_base` in.
    assert isinstance(rt._governance._base, SkillBackedGovernanceProvider)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_bound_skill_runs_and_its_verdict_is_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end on the mock model: the binding evaluates the skill (the mock answers "mock
    response", an unrecognised verdict, which reads as pass with a warning — so the run proceeds),
    and the `decision.evaluated` event reaches the durable audit through the wrapper."""
    import sqlite3  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(_WS)
    (ws / "skills" / "content-filter.yaml").write_text(_SKILL)
    (ws / "topologies" / "hello.yaml").write_text(_TOPO)
    rt = WorkspaceRuntime.from_workspace_path(ws)
    result = await rt.run("hello", "hi")
    kinds = [e.event_type for e in result.events]
    assert "decision.evaluated" in kinds, kinds
    rows = (
        sqlite3.connect(ws / ".swarmkit" / "audit.sqlite")
        .execute(
            "select event_type, skill_id from audit_events where event_type = 'decision.evaluated'"
        )
        .fetchall()
    )
    assert rows == [("decision.evaluated", "content-filter")], rows


@pytest.mark.asyncio
async def test_post_output_gate_applies_when_the_agent_used_a_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The turn that calls a tool comes back through the tool loop as a dict, and that branch
    returned before the post_output block: an agent that did its work with tools was never judged.
    Scripted here: first turn calls `note`, the loop's synthesis answers in text; the post_output
    binding must still have evaluated that answer."""
    from swarmkit_runtime.model_providers import (  # noqa: PLC0415
        CompletionResponse,
        ContentBlock,
        MockModelProvider,
        Usage,
    )

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    ws = tmp_path / "ws"
    (ws / "skills").mkdir(parents=True)
    (ws / "topologies").mkdir()
    (ws / "workspace.yaml").write_text(_WS_POST)
    (ws / "skills" / "content-filter.yaml").write_text(_SKILL)
    (ws / "skills" / "note.yaml").write_text(_TOOL_SKILL)
    (ws / "topologies" / "hello.yaml").write_text(_TOPO_TOOL)
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
    # The judge runs on the wrapper's own provider; point it at the mock too.
    rt._governance._model_provider = provider  # type: ignore[attr-defined]

    result = await rt.run("hello", "hi")
    decided = [
        e
        for e in result.events
        if e.event_type == "decision.evaluated" and e.payload.get("trigger") == "post_output"
    ]
    # On the unfixed code this is [] — the dict-result branch returned first.
    assert decided, [(e.event_type, e.payload.get("trigger")) for e in result.events]
    assert result.output
