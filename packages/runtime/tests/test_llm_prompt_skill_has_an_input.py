"""An `llm_prompt` skill's tool asks for the text it works on.

With no `inputs` declared the skill was offered to the model with an EMPTY parameter schema, so
every model called it with `{}`: `summarize {}` came back "no text was provided", and
`governed-memory {}` proposed nothing eight turns in a row. The tool now carries one required
`input` string (a skill wanting structured arguments declares `inputs` itself), and a single
`input` argument reaches the prompt as plain text rather than as `{"input": "..."}`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from swarmkit_runtime.langgraph_compiler._prompts import _build_tools
from swarmkit_runtime.resolver import ResolvedAgent
from swarmkit_runtime.skills import ResolvedSkill


def _skill(skill_id: str, *, inputs: dict[str, Any] | None = None) -> ResolvedSkill:
    raw = SimpleNamespace(
        implementation={"type": "llm_prompt", "prompt": "Summarize."},
        inputs=inputs,
        category="capability",
    )
    return ResolvedSkill(id=skill_id, raw=raw, source_path=Path("x"))  # type: ignore[arg-type]


def _agent(*skills: ResolvedSkill) -> ResolvedAgent:
    return ResolvedAgent(
        id="a", role="root", model={"name": "m"}, prompt={"system": "s"}, skills=skills, iam=None
    )


def test_an_undeclared_llm_prompt_skill_takes_one_input_string() -> None:
    tools = _build_tools(_agent(_skill("summarize")))
    summarize = next(t for t in tools if t.name == "summarize")
    # On the unfixed code this was `{}` — nothing for the model to fill in.
    assert summarize.input_schema["required"] == ["input"]
    assert summarize.input_schema["properties"]["input"]["type"] == "string"


def test_a_declared_inputs_schema_wins() -> None:
    declared = {
        "type": "object",
        "properties": {"facts": {"type": "string"}},
        "required": ["facts"],
    }
    tools = _build_tools(_agent(_skill("remember", inputs=declared)))
    assert next(t for t in tools if t.name == "remember").input_schema == declared
