"""The system prompt half of structured output: what it says, and when it says nothing.

Three defects found by capturing the payload SwarmKit actually sends to Ollama for a router topology
whose only job is to classify a sentence:

* the system prompt arrived at 5112 characters against the topology's 2538 — the compiler pasted
  the whole JSON schema in as prose *while* the provider already constrained decoding to it;
* the system prompt arrived at 5112 characters against the topology's 2538 — the compiler
  pasted the whole JSON schema in as prose *while* the provider already constrained it;
  must have a 'fact' and 'source' field"), hardcoded and appended to every structured prompt;
* `max_tokens`, a documented field on both the topology and archetype schemas, reached no provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from swarmkit_runtime.langgraph_compiler._prompts import (
    _build_completion_request,
    _build_system_prompt,
    _structured_output_instruction,
)
from swarmkit_runtime.model_providers import Message
from swarmkit_runtime.model_providers._registry import provider_enforces_response_schema

SCHEMA = {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}}}
MESSAGES = [Message(role="user", content="is anyone at the main door")]


@dataclass
class _Agent:
    id: str = "router"
    role: str = "root"
    model: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    output_schema_disabled: bool = False
    skills: list[Any] = field(default_factory=list)
    prompt: dict[str, Any] = field(default_factory=lambda: {"system": "You route messages."})
    children: list[Any] = field(default_factory=list)


# ---- no other agent's schema in your prompt ----------------------------------------------------


def test_no_foreign_schema_instructions_leak_in() -> None:
    """A router classifying a sentence was being told to emit `findings` with `fact` and `source`
    fields its schema does not have. The text was hardcoded, so EVERY structured agent got it."""
    for has_tools in (True, False):
        out = _structured_output_instruction(SCHEMA, has_tools)
        for foreign in ("finding", "fact", "source", "not_found"):
            assert foreign not in out, (has_tools, foreign)


# ---- say it once ------------------------------------------------------------------------------


def test_the_compiler_always_describes_the_schema() -> None:
    """Skipping the paste when the provider constrains decoding was tried and reverted: it cut
    latency 27% and dropped a router's `query` intent from 55% to 29%. GBNF constrains shape and
    drops `description`, and those descriptions are what tell a small model which intent it is
    looking at. This pins the behaviour the measurement chose."""
    agent = _Agent(model={"provider": "ollama", "name": "m"}, output_schema=SCHEMA)
    prompt = _build_system_prompt(agent, [])  # type: ignore[arg-type]
    assert prompt and "kind" in prompt


def test_the_grammar_enforced_flag_still_suppresses_when_asked_directly() -> None:
    """Kept for a caller whose schema has no descriptions worth carrying — not used by the
    compiler."""
    assert _structured_output_instruction(SCHEMA, False, grammar_enforced=True) == ""


def test_an_enforced_schema_still_says_when_on_a_tool_turn() -> None:
    """The grammar cannot express "final answer only", so that part stays — minus the schema."""
    out = _structured_output_instruction(SCHEMA, True, grammar_enforced=True)
    assert "final answer" in out.lower()
    assert "kind" not in out  # no schema body


def test_an_unenforced_schema_is_still_described_in_full() -> None:
    """Anthropic ignores `response_format` entirely, so for it the prose is the only thing carrying
    the shape. Removing it there would break structured output outright."""
    out = _structured_output_instruction(SCHEMA, False)
    assert "kind" in out
    assert "json" in out.lower()


def test_no_schema_means_no_instruction() -> None:
    assert _structured_output_instruction(None, False) == ""
    assert _structured_output_instruction(None, True, grammar_enforced=True) == ""


# ---- which providers enforce -------------------------------------------------------------------


def test_providers_that_constrain_decoding_are_declared() -> None:
    assert provider_enforces_response_schema("ollama") is True
    assert provider_enforces_response_schema("openai") is True
    assert provider_enforces_response_schema("google") is True


def test_anthropic_does_not_constrain_decoding() -> None:
    """It never reads `response_format`; declaring otherwise strips the prompt it depends on."""
    assert provider_enforces_response_schema("anthropic") is False


def test_an_unknown_provider_is_assumed_not_to_enforce() -> None:
    """The conservative direction: guessing "enforced" would silently remove the only thing
    carrying the schema for a provider that needed it."""
    assert provider_enforces_response_schema("some-third-party-provider") is False
    assert provider_enforces_response_schema("") is False


# ---- max_tokens --------------------------------------------------------------------------------


def test_max_tokens_reaches_the_request() -> None:
    """Documented on the topology and archetype schemas, validated on load, and read by nobody — so
    an author who capped a node got no cap."""
    agent = _Agent(model={"provider": "ollama", "name": "m", "max_tokens": 256})
    req = _build_completion_request("m", MESSAGES, "sys", [], agent)  # type: ignore[arg-type]
    assert req.max_tokens == 256


def test_max_tokens_is_optional() -> None:
    agent = _Agent(model={"provider": "ollama", "name": "m"})
    req = _build_completion_request("m", MESSAGES, "sys", [], agent)  # type: ignore[arg-type]
    assert req.max_tokens is None


def test_temperature_still_reaches_the_request() -> None:
    agent = _Agent(model={"provider": "ollama", "name": "m", "temperature": 0})
    req = _build_completion_request("m", MESSAGES, "sys", [], agent)  # type: ignore[arg-type]
    assert req.temperature == 0
