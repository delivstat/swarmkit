"""A harness agent's output is exactly what the agent wrote, and it is told its contract.

Bug 16, in two halves.

**The suffix.** A successful harness result had ` (+<N> bytes diff)` appended. For a schema-bound
agent whose contract is JSON that makes the artifact unparseable, so
``_enforce_harness_output_schema`` reported::

    (root): output is not valid JSON

instead of the field-specific errors it exists to produce. The correction retries then carried the
one message the agent cannot act on — it was never told which fields were missing — so both
attempts repeated it, exhausted, and the text passed through annotated. The retries could not
succeed by construction, and each is a full agent session.

This is the same defect as bug 13's ``[harness:<kind>]`` prefix, at the other end of the string: a
display artifact the recorder adds, baked into output the agent never wrote. On non-schema runs it
was merely cosmetic, which is how it survived — harmless noise until the contract is machine
checked. ``diff_bytes`` was already in the ``executor.result`` audit payload the whole time.

**The contract.** ``output_schema`` on a harness was a post-hoc check only: the agent was never
shown the shape it was being held to, and had to discover it from correction feedback. One run
invented its own 7-key document against a declared 22-field schema — a reasonable thing to do when
nothing said otherwise. Stating the contract up front is what makes the FIRST attempt likely to
conform, rather than the third.
"""

from __future__ import annotations

import json
from typing import Any

from swarmkit_runtime.langgraph_compiler._harness_node import (
    _harness_output_schema,
    _output_contract,
    _task_spec,
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "scope"],
    "properties": {"summary": {"type": "string"}, "scope": {"type": "string"}},
}


class _Agent:
    def __init__(
        self, schema: dict[str, Any] | None = None, *, disabled: bool = False, role: str = "worker"
    ) -> None:
        self.id = "designer"
        self.role = role
        self.skills: list[Any] = []
        self.output_schema = schema
        self.output_schema_disabled = disabled


def _statement(agent: Any, text: str = "design the WMS change") -> str:
    return _task_spec(agent, {"input": text}, None).statement


# ---- the suffix is gone from the artifact -----------------------------------------------------


def test_the_annotation_no_longer_appears_in_the_source() -> None:
    """Stated against the file because the annotation's whole failure mode was looking harmless:
    it read as noise on prose runs and only broke anything once a contract was machine-checked."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    # The only surviving mention is the comment explaining why it was removed.
    appends = [
        line
        for line in src.splitlines()
        if "bytes diff" in line and not line.strip().startswith("#")
    ]
    assert not appends, f"a display annotation is being appended again: {appends}"


def test_json_output_with_a_diff_would_still_parse() -> None:
    """The concrete failure: the emitted JSON parses, the stored artifact did not."""
    emitted = json.dumps({"summary": "s", "scope": "the WMS flow"})

    annotated = f"{emitted} (+400 bytes diff)"

    # What the old artifact looked like, and why enforcement reported the wrong error.
    try:
        json.loads(annotated)
        raise AssertionError("the annotated form should NOT parse — that was the bug")
    except json.JSONDecodeError:
        pass
    assert json.loads(emitted)["summary"] == "s"


# ---- the agent is told its contract -----------------------------------------------------------


def test_the_schema_is_stated_in_the_task() -> None:
    """The bug's second half: a harness agent was never shown the shape it had to produce."""
    statement = _statement(_Agent(SCHEMA))

    assert "output-contract" in statement
    assert '"summary"' in statement
    assert '"scope"' in statement


def test_the_original_statement_survives_intact() -> None:
    """The contract is appended, not substituted — the task is still the task."""
    statement = _statement(_Agent(SCHEMA), "design the WMS change")

    assert statement.startswith("design the WMS change")


def test_the_contract_is_delimited_and_attributed() -> None:
    """Same reason retry envelopes are: the statement above is the user's, this is the runtime's,
    and an agent that cannot tell them apart is being asked to trust unattributed instructions."""
    contract = _output_contract(SCHEMA)

    assert contract.startswith("<output-contract>")
    assert contract.rstrip().endswith("</output-contract>")


def test_an_agent_without_a_schema_gets_an_unchanged_task() -> None:
    """No schema declared, nothing added. A harness that produces a diff must not be handed a
    JSON-only instruction it was never meant to satisfy."""
    assert _statement(_Agent(None)) == "design the WMS change"


def test_an_opted_out_agent_gets_an_unchanged_task() -> None:
    """`output_schema: null` means opt out — the contract must not reappear in the prompt."""
    assert _statement(_Agent(SCHEMA, disabled=True)) == "design the WMS change"


def test_an_empty_statement_stays_empty() -> None:
    """An empty task is surfaced as such rather than being turned into a schema-only prompt that
    would run, cost money, and produce a plausible artifact from no instruction at all."""
    assert _statement(_Agent(SCHEMA), "") == ""


# ---- the explicit-only rule still holds -------------------------------------------------------


def test_a_worker_without_a_schema_does_not_inherit_the_platform_default() -> None:
    """`role: worker` + `kind: harness` with no schema is the `examples/sdlc-pipeline` developer,
    which produces a diff, not findings. Applying the model path's worker default here would fail
    every run against a contract nobody wrote."""
    assert _harness_output_schema(_Agent(None, role="worker")) is None


def test_an_explicit_schema_is_enforced() -> None:
    assert _harness_output_schema(_Agent(SCHEMA)) == SCHEMA


def test_an_opt_out_disables_enforcement() -> None:
    assert _harness_output_schema(_Agent(SCHEMA, disabled=True)) is None
