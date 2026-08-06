"""A correction loop stops when the executor is broken, and says so.

Bug 19. When a schema-correction re-invocation failed for an infrastructure reason — the sandbox
never came up, the MCP gateway was never reached — enforcement could not tell that apart from "the
agent revised its answer and it is still wrong". So it retried, hit the same fault, retried again,
exhausted its budget, and recorded an `output.schema_violation` naming a schema problem that was
never the cause.

From the reported run::

    output.schema_reinvoke    attempt=1 prior_draft_chars=236
    executor.mcp_unreachable  advertised=33 listed=0 called=0     <- the runtime KNEW
    output.schema_reinvoke    attempt=2 prior_draft_chars=236     <- and re-invoked anyway
    executor.mcp_unreachable  advertised=33 listed=0 called=0
    output.schema_violation   errors=['(root): output is not valid JSON']

$0.55 on two sessions that could not have succeeded, and a final record pointing at the model
instead of the infrastructure. An operator reading it concludes the agent cannot produce conforming
JSON; what happened is that two executions never ran the task at all.

The information was in hand twice over. `_reinvoke` reads `node_errors`, correctly discards the
failed run — and then returns a `str`, which cannot express "this did not run". And
`prior_draft_chars` was byte-identical across consecutive corrections, which alone is enough to
stop.

Both are now used: `_reinvoke` raises, and an unchanged draft ends the loop independently.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from swarmkit_runtime.langgraph_compiler._compiler import (
    ExecutorUnavailable,
    _enforce_harness_output_schema,
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["documents"],
    "properties": {"documents": {"type": "array"}},
}
BAD = '{"documents": "not an array"}'
GOOD = json.dumps({"documents": ["a"]})


class _Governance:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record_event(self, event: Any) -> None:
        self.events.append(event)


def _types(gov: _Governance) -> list[str]:
    return [e.event_type for e in gov.events]


async def _enforce(text: str, reinvoke: Any, gov: Any) -> str:
    return await _enforce_harness_output_schema(
        text, SCHEMA, agent_id="documenter", governance=gov, reinvoke=reinvoke
    )


# ---- a broken executor ends the loop -----------------------------------------------------------


@pytest.mark.asyncio
async def test_the_loop_stops_on_the_first_infrastructure_failure() -> None:
    """The money. Two further sessions used to be spent on an executor that could not succeed."""
    calls = 0

    async def reinvoke(_feedback: str) -> str:
        nonlocal calls
        calls += 1
        raise ExecutorUnavailable("documenter: the MCP gateway was never reached")

    await _enforce(BAD, reinvoke, _Governance())

    assert calls == 1, "the remaining attempts must not be spent"


@pytest.mark.asyncio
async def test_the_outcome_names_the_real_cause() -> None:
    """Not `output.schema_violation`. An operator reading that concludes the agent cannot produce
    conforming JSON, when the executions never ran the task."""
    gov = _Governance()

    async def reinvoke(_f: str) -> str:
        raise ExecutorUnavailable("documenter: the MCP gateway was never reached")

    await _enforce(BAD, reinvoke, gov)

    assert "output.schema_abandoned" in _types(gov)
    assert "output.schema_violation" not in _types(gov)


@pytest.mark.asyncio
async def test_the_record_carries_the_reason_and_the_schema_errors() -> None:
    """The schema errors are context, not the headline — keeping them is right, leading with them
    is what misdirected the diagnosis."""
    gov = _Governance()

    async def reinvoke(_f: str) -> str:
        raise ExecutorUnavailable("documenter: the MCP gateway was never reached")

    await _enforce(BAD, reinvoke, gov)

    payload = next(e for e in gov.events if e.event_type == "output.schema_abandoned").payload
    assert "gateway" in payload["reason"]
    assert any("documents" in err for err in payload["schema_errors"])


@pytest.mark.asyncio
async def test_the_text_says_the_contract_was_not_enforced() -> None:
    """Distinct from a violation: nobody checked, which is not the same as checked and failed."""

    async def reinvoke(_f: str) -> str:
        raise ExecutorUnavailable("sandbox did not start")

    out = await _enforce(BAD, reinvoke, _Governance())

    assert "OUTPUT SCHEMA NOT ENFORCED" in out
    assert "sandbox did not start" in out


# ---- an unmoving correction ends the loop too --------------------------------------------------


@pytest.mark.asyncio
async def test_an_identical_draft_stops_the_loop() -> None:
    """The cheap belt-and-braces the report asks for, independent of `node_errors`: byte-identical
    output across a correction means the re-invocation changed nothing."""
    calls = 0

    async def reinvoke(_f: str) -> str:
        nonlocal calls
        calls += 1
        return BAD  # the same draft back again

    gov = _Governance()
    await _enforce(BAD, reinvoke, gov)

    assert calls == 1
    # `stalled`, not `abandoned` — the executor worked, the answer did not improve. The contract
    # WAS checked and did fail, so the violation still stands.
    assert "output.schema_stalled" in _types(gov)
    assert "output.schema_violation" in _types(gov)


# ---- the mechanism still works ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_working_correction_still_runs_to_success() -> None:
    """This is not "stop retrying" — a re-invocation that genuinely improves must still be used."""
    gov = _Governance()

    async def reinvoke(_f: str) -> str:
        return GOOD

    out = await _enforce(BAD, reinvoke, gov)

    assert json.loads(out) == {"documents": ["a"]}
    assert "output.schema_abandoned" not in _types(gov)


@pytest.mark.asyncio
async def test_a_genuinely_wrong_second_answer_still_exhausts_normally() -> None:
    """A DIFFERENT wrong answer each time is the agent failing, not the executor — that path must
    still run its budget and end in a violation."""
    replies = iter(['{"documents": 1}', '{"documents": 2}'])

    async def reinvoke(_f: str) -> str:
        return next(replies)

    gov = _Governance()
    await _enforce(BAD, reinvoke, gov)

    assert "output.schema_violation" in _types(gov)
    assert "output.schema_abandoned" not in _types(gov)


# ---- the signal exists at the seam ---------------------------------------------------------------


def test_reinvoke_raises_rather_than_returning_the_old_text() -> None:
    """Stated against the source: returning the previous string is exactly what made the loop
    unable to tell a dead executor from a stubborn agent."""
    from pathlib import Path  # noqa: PLC0415

    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/langgraph_compiler/_compiler.py"
    ).read_text()

    assert "raise ExecutorUnavailable(" in src
    assert 'if retried.get("node_errors"):\n            return str(' not in src
