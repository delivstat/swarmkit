"""Schema enforcement on a harness must not discard a good artifact to buy a worse one.

Bug 17. A single-agent topology ran its harness twice. The first execution produced a complete,
schema-valid, fully-cited artifact — `status: success`, $1.44, 13 minutes. The runtime then ran the
same agent again from scratch; the second produced nine "no source access" stubs, which also
validated, and *that* became the run's output. Nothing recorded that the first existed.

Three defects, each fixed here.

**The capture parsed strictly.** `json.loads` is right where a GRAMMAR guarantees bare JSON — the
model path sets `response_format: json_schema`, so a provider cannot emit anything else. Nothing
guarantees it on the harness path: a CLI agent's final message is free text, and the
`<output-contract>` added in 1.156.0 is a request, not a constraint. An agent that wraps its object
in a ```json fence produces output that "is not valid JSON" while its content is perfectly valid —
and enforcement then spends a whole harness session correcting an artifact that was already right.

**The last attempt won unconditionally.** No comparison, so valid-but-empty beat valid-and-complete
purely by arriving second.

**The re-invocation was invisible.** `output.schema_violation` is written only when retries are
*exhausted*; a re-run that then succeeded left an operator looking at two executions and no reason
for the second.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from swarmkit_runtime.langgraph_compiler._compiler import _enforce_harness_output_schema
from swarmkit_runtime.skills._output_validator import extract_json_object

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["documents"],
    "properties": {"documents": {"type": "array", "items": {"type": "string"}}},
}

GOOD = json.dumps({"documents": ["a", "b", "c"]})
ALSO_VALID_BUT_EMPTY = json.dumps({"documents": []})


class _Governance:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record_event(self, event: Any) -> None:
        self.events.append(event)


def _reinvoker(*replies: str) -> Any:
    """A harness that returns each reply in turn, counting how often it was made to run."""
    calls: list[str] = []

    async def reinvoke(feedback: str) -> str:
        calls.append(feedback)
        return replies[min(len(calls) - 1, len(replies) - 1)]

    reinvoke.calls = calls  # type: ignore[attr-defined]
    return reinvoke


async def _enforce(text: str, reinvoke: Any, gov: Any) -> str:
    return await _enforce_harness_output_schema(
        text, SCHEMA, agent_id="documenter", governance=gov, reinvoke=reinvoke
    )


# ---- the capture tolerates what a CLI agent actually emits -------------------------------------


def test_a_bare_object_parses() -> None:
    assert extract_json_object(GOOD) == {"documents": ["a", "b", "c"]}


@pytest.mark.parametrize(
    "wrapper",
    [
        "```json\n{body}\n```",
        "```\n{body}\n```",
        "Here are the documents:\n\n{body}",
        "{body}\n\nLet me know if you need changes.",
        "I've written all nine.\n\n```json\n{body}\n```\n\nDone.",
    ],
    ids=["json-fence", "bare-fence", "preamble", "postscript", "both"],
)
def test_the_shapes_an_agent_actually_returns_are_parsed(wrapper: str) -> None:
    """Each of these is valid content that a strict `json.loads` rejects — and rejecting it cost a
    full harness session."""
    assert extract_json_object(wrapper.format(body=GOOD)) == {"documents": ["a", "b", "c"]}


def test_text_with_no_object_is_still_a_failure() -> None:
    """Tolerance is not repair. Output that contains no object has genuinely failed its contract."""
    assert extract_json_object("I could not complete the task.") is None


def test_malformed_json_is_not_repaired() -> None:
    assert extract_json_object('{"documents": [') is None


def test_a_bare_array_is_not_an_object() -> None:
    """A contract that says `object` means it — this must stay a validation failure, not be
    coerced into passing."""
    assert extract_json_object("[1, 2, 3]") is None


@pytest.mark.asyncio
async def test_a_fenced_but_valid_artifact_is_accepted_without_a_rerun() -> None:
    """The root cause, end to end: this used to trigger a whole new harness session."""
    gov = _Governance()
    reinvoke = _reinvoker("never used")

    out = await _enforce(f"```json\n{GOOD}\n```", reinvoke, gov)

    assert reinvoke.calls == [], "a valid artifact must not be re-run"
    assert extract_json_object(out) == {"documents": ["a", "b", "c"]}


# ---- a good result is not traded for a worse one -----------------------------------------------


@pytest.mark.asyncio
async def test_the_first_passing_attempt_is_kept() -> None:
    gov = _Governance()

    out = await _enforce(GOOD, _reinvoker(ALSO_VALID_BUT_EMPTY), gov)

    assert extract_json_object(out) == {"documents": ["a", "b", "c"]}


@pytest.mark.asyncio
async def test_a_genuinely_invalid_artifact_is_still_corrected() -> None:
    """The mechanism still works — this is not "stop enforcing"."""
    gov = _Governance()
    reinvoke = _reinvoker(GOOD)

    out = await _enforce('{"documents": "not an array"}', reinvoke, gov)

    assert len(reinvoke.calls) == 1
    assert extract_json_object(out) == {"documents": ["a", "b", "c"]}


@pytest.mark.asyncio
async def test_exhaustion_still_annotates_and_records() -> None:
    gov = _Governance()

    out = await _enforce("no json here", _reinvoker("still none", "still none"), gov)

    assert "OUTPUT SCHEMA VIOLATIONS" in out
    assert any(e.event_type == "output.schema_violation" for e in gov.events)


# ---- a re-run is never invisible ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reinvocation_is_audited() -> None:
    """A whole harness session — minutes and dollars — used to happen with no record at all when
    the retry then succeeded."""
    gov = _Governance()

    await _enforce('{"documents": 5}', _reinvoker(GOOD), gov)

    reinvokes = [e for e in gov.events if e.event_type == "output.schema_reinvoke"]
    assert len(reinvokes) == 1
    assert reinvokes[0].agent_id == "documenter"


@pytest.mark.asyncio
async def test_the_audit_says_which_attempt_and_why() -> None:
    gov = _Governance()

    await _enforce('{"documents": 5}', _reinvoker(GOOD), gov)

    payload = next(e for e in gov.events if e.event_type == "output.schema_reinvoke").payload
    assert payload["attempt"] == 1
    assert any("documents" in err for err in payload["errors"])


@pytest.mark.asyncio
async def test_the_audit_records_the_prior_drafts_size() -> None:
    """So "correction" degrading into "start over" is visible: the second session here was handed
    56 KB of finished work and behaved as though it had nothing."""
    gov = _Governance()
    draft = '{"documents": 5}'

    await _enforce(draft, _reinvoker(GOOD), gov)

    payload = next(e for e in gov.events if e.event_type == "output.schema_reinvoke").payload
    assert payload["prior_draft_chars"] == len(draft)


@pytest.mark.asyncio
async def test_each_attempt_is_recorded_separately() -> None:
    gov = _Governance()

    await _enforce("no json", _reinvoker("still none", GOOD), gov)

    attempts = [
        e.payload["attempt"] for e in gov.events if e.event_type == "output.schema_reinvoke"
    ]
    assert attempts == [1, 2]


# ---- the trace can tell a full artifact from an empty one --------------------------------------


def test_the_trace_step_reports_the_result_and_the_diff_separately() -> None:
    """`result_length` was `len(diff) or len(output)`, so two executions producing 56 KB and 14 KB
    both recorded 400 — the runtime's own scratch files. The one field an operator would reach for
    to spot this bug could not see it."""
    from swarmkit_runtime.trace import AgentStep  # noqa: PLC0415

    step = AgentStep(agent_id="documenter", result_length=56_000, diff_length=400)

    assert step.result_length == 56_000
    assert step.diff_length == 400


def test_the_node_no_longer_conflates_them() -> None:
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1]
        / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
    ).read_text()

    assert "len(diff) or (len(output)" not in src
    assert "diff_length=diff_length" in src


def test_the_timestamp_helper_is_available() -> None:
    """Guards the import the audit event needs — a NameError here would be raised inside the
    best-effort path and lose the record it exists to write."""
    assert isinstance(datetime.now(tz=UTC), datetime)
