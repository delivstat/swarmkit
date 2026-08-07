"""A harness's artifact is found wherever in the session it was produced.

Bug 17, root cause. A harness's captured output is its FINAL message — the adapter maps `output`
from Claude Code's `$.result`. An agent that emits a large artifact and then signs off ("the nine
documents are above, each with citations…") hands the runtime the closing remark.

The reported run: a 68 KB conforming artifact produced, `prior_draft_chars=236` recorded. Schema
enforcement saw no JSON, re-ran the agent, and the re-run — which came up with no tools (bug 18) —
returned stubs that also validated and won. A correct, expensive artifact was discarded because the
agent said one polite sentence after producing it.

The messages were in hand the whole time. `round_messages` collected every one and used them for a
single yes/no question-detection check, then dropped them. Same shape as the rest of this class:
information exists, nothing surfaces it, and the absence is blamed on the agent.

Conformance is the test, not braces: an intermediate sketch or a tool-call echo must not outrank the
real artifact.
"""

from __future__ import annotations

import json
from typing import Any

from swarmkit_runtime.langgraph_compiler._harness_node import _artifact_from_session

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["documents"],
    "properties": {
        "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
}

ARTIFACT = json.dumps({"documents": ["a", "b", "c"]})
SIGN_OFF = (
    "The nine documents are above, each with citations to the WMS tables and services "
    "involved. Let me know if you would like any section expanded."
)


# ---- the artifact is recovered ------------------------------------------------------------------


def test_the_final_message_is_used_when_it_conforms() -> None:
    """The common case must not change: an agent that ends with its artifact is already right."""
    out, recovered = _artifact_from_session(ARTIFACT, [ARTIFACT], SCHEMA)

    assert out == ARTIFACT
    assert recovered is False


def test_an_artifact_produced_before_a_sign_off_is_found() -> None:
    """The bug, exactly: 68 KB produced, 236 characters captured."""
    out, recovered = _artifact_from_session(SIGN_OFF, [ARTIFACT, SIGN_OFF], SCHEMA)

    assert json.loads(out) == {"documents": ["a", "b", "c"]}
    assert recovered is True


def test_a_fenced_artifact_is_found() -> None:
    """A mid-session message usually IS fenced — the agent is writing prose around it."""
    fenced = f"Here they are:\n\n```json\n{ARTIFACT}\n```"

    out, _ = _artifact_from_session(SIGN_OFF, [fenced, SIGN_OFF], SCHEMA)

    assert json.loads(out) == {"documents": ["a", "b", "c"]}


def test_the_latest_conforming_artifact_wins() -> None:
    """An agent that revises mid-session produces two; the later one is its answer."""
    first = json.dumps({"documents": ["draft"]})
    second = json.dumps({"documents": ["final"]})

    out, _ = _artifact_from_session(SIGN_OFF, [first, second, SIGN_OFF], SCHEMA)

    assert json.loads(out) == {"documents": ["final"]}


# ---- conformance, not braces --------------------------------------------------------------------


def test_a_non_conforming_object_does_not_outrank_the_artifact() -> None:
    """A tool-call echo or an intermediate sketch has braces and is not the artifact. Selecting on
    "has JSON" would let the last such message win — which is how a correct run would be replaced
    by a fragment."""
    echo = '{"query": "pgm_hold", "limit": 40}'

    out, _ = _artifact_from_session(SIGN_OFF, [ARTIFACT, echo, SIGN_OFF], SCHEMA)

    assert json.loads(out) == {"documents": ["a", "b", "c"]}


def test_an_empty_documents_list_does_not_conform() -> None:
    """`minItems` is part of the contract, so a plausible-but-empty object is not the artifact."""
    empty = json.dumps({"documents": []})

    out, recovered = _artifact_from_session(SIGN_OFF, [ARTIFACT, empty, SIGN_OFF], SCHEMA)

    assert json.loads(out) == {"documents": ["a", "b", "c"]}
    assert recovered is True


# ---- it recovers, it does not invent -------------------------------------------------------------


def test_nothing_conforming_leaves_the_output_alone() -> None:
    """Enforcement must still report on what the agent actually produced. This recovers a lost
    artifact; it does not manufacture one."""
    out, recovered = _artifact_from_session("I could not complete the task.", ["nor here"], SCHEMA)

    assert out == "I could not complete the task."
    assert recovered is False


def test_no_messages_at_all_is_safe() -> None:
    out, recovered = _artifact_from_session(SIGN_OFF, [], SCHEMA)

    assert out == SIGN_OFF
    assert recovered is False


def test_the_recovered_artifact_carries_no_surrounding_prose() -> None:
    """The declared contract is the object. Carrying the message's commentary forward would put it
    into the stage artifact and the next stage's input — the defect bug 16 fixed, one layer up."""
    fenced = f"Here is the result:\n\n```json\n{ARTIFACT}\n```\n\nHope that helps."

    out, _ = _artifact_from_session(SIGN_OFF, [fenced, SIGN_OFF], SCHEMA)

    assert "Hope that helps" not in out
    assert "```" not in out
    assert json.loads(out) == {"documents": ["a", "b", "c"]}


# ---- it is wired, and only for schema-bound agents ------------------------------------------


def test_the_node_collects_messages_for_the_whole_session() -> None:
    """They were reset each round and used only for question detection. An artifact produced in an
    earlier round would still have been lost."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "session_messages.append(event.text)" in src
    assert "session_messages: list[str] = []" in src


def test_recovery_is_audited() -> None:
    """A run whose artifact came from an earlier message is one whose final message was not the
    artifact — worth seeing, since it used to cost a correction session to discover."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "executor.artifact_recovered" in src


def test_an_agent_without_a_schema_is_untouched() -> None:
    """No declared contract, nothing to conform to — the final message stands, and a harness that
    produces a diff is not rummaged through for JSON."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "if schema is not None:" in src
