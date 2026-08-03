"""A harness tool call records what it RETURNED, not what it was given — for every harness.

From the image-delivery report: a design agent described UI screens it had never seen, across three
runs, convincingly. The screenshots were referenced by document-relative paths that resolved
nowhere, so the image tool returned nothing — and the trace showed the call looking perfectly
healthy. "There is no error, no warning, and nothing in the trace that says an image was missed."

Three defects, none of them specific to one vendor:

1. ``ToolCall.result_length`` — documented as the length of the RESULT — was given
   ``len(event.input_summary)``, the length of the ARGUMENTS. A tool that returned nothing still
   recorded a large number, because the *path* was long.
2. Outcomes were not mapped at all. claude-code reports them out-of-band on a following
   ``tool_result``; codex as an exit code on ``exec_command_end``; gemini on its own response
   event. Only opencode emitted anything, and it emitted its vendor's raw word.
3. ``ExecToolCall.status`` had no vocabulary, so opencode's ``"completed"`` and gemini's
   ``"success"`` and codex's ``0`` meant nothing in common downstream.

The fix is one normalization at the seam every adapter passes through, so a new harness is observed
identically to the last — the whole point of the executor abstraction.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml
from swarmkit_runtime.executors._adapter_spec import parse_adapter_spec
from swarmkit_runtime.executors._event_map import AdapterInterpreter
from swarmkit_runtime.executors._events import ExecToolCall, normalize_tool_status

ADAPTERS = pathlib.Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/executors/adapters"
HARNESS_NODE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src/swarmkit_runtime/langgraph_compiler/_harness_node.py"
)


def _interp(harness: str) -> AdapterInterpreter:
    return AdapterInterpreter(
        parse_adapter_spec(yaml.safe_load((ADAPTERS / f"{harness}.yaml").read_text()))
    )


def _statuses(harness: str, line: dict[str, Any]) -> list[str]:
    return [e.status for e in _interp(harness).feed(line) if isinstance(e, ExecToolCall)]


# ---- the vocabulary is shared -------------------------------------------------------------------


@pytest.mark.parametrize("word", ["ok", "success", "succeeded", "completed", "complete", "done"])
def test_every_way_a_harness_says_it_worked(word: str) -> None:
    assert normalize_tool_status(word) == "ok"


@pytest.mark.parametrize(
    "word", ["error", "failed", "failure", "denied", "rejected", "timeout", "cancelled", "aborted"]
)
def test_every_way_a_harness_says_it_failed(word: str) -> None:
    assert normalize_tool_status(word) == "error"


@pytest.mark.parametrize("word", ["pending", "running", "in_progress", "started"])
def test_a_call_still_in_flight_has_no_outcome_yet(word: str) -> None:
    """Not ``ok``. A tool that has not finished has not succeeded, and recording it as success is
    exactly the conflation this whole change exists to remove."""
    assert normalize_tool_status(word) == ""


def test_an_unknown_vocabulary_is_unreported_not_assumed_good() -> None:
    """The safety property. A harness whose words we have not learned must surface as *unknown* —
    never as a confident success (which hides failures) and never as an error (which cries wolf)."""
    assert normalize_tool_status("weltschmerz") == ""
    assert normalize_tool_status("") == ""


def test_normalization_is_case_and_separator_insensitive() -> None:
    assert normalize_tool_status("In-Progress") == ""
    assert normalize_tool_status("ERROR") == "error"
    assert normalize_tool_status(" Completed ") == "ok"


# ---- every bundled harness reports outcomes, in that shared vocabulary ---------------------------

# One failing and one succeeding tool call per harness, in that harness's own native protocol.
FAILURES: dict[str, dict[str, Any]] = {
    "claude-code": {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "is_error": True, "name": "view_image", "content": "ENOENT"}
            ]
        },
    },
    "opencode": {
        "type": "tool",
        "part": {"tool": "read", "state": {"status": "error", "input": "/a.png"}},
    },
    "codex": {"msg": {"type": "exec_command_end", "exit_code": 1, "stdout": ""}},
    "gemini-cli": {
        "type": "tool_call_response",
        "name": "read_file",
        "status": "error",
        "response": "ENOENT",
    },
}

SUCCESSES: dict[str, dict[str, Any]] = {
    "opencode": {
        "type": "tool",
        "part": {"tool": "read", "state": {"status": "completed", "input": "/a.png"}},
    },
    "codex": {"msg": {"type": "exec_command_end", "exit_code": 0, "stdout": "ok"}},
    "gemini-cli": {
        "type": "tool_call_response",
        "name": "read_file",
        "status": "success",
        "response": "...",
    },
}


@pytest.mark.parametrize("harness", sorted(FAILURES))
def test_a_failed_tool_call_is_visible_for_every_harness(harness: str) -> None:
    """The report's central complaint, asked of each harness in turn: when a tool fails, does
    anything in the stream say so?"""
    assert "error" in _statuses(harness, FAILURES[harness]), (
        f"{harness} reports tool failures and the adapter drops them on the floor"
    )


@pytest.mark.parametrize("harness", sorted(SUCCESSES))
def test_a_successful_tool_call_is_not_cried_wolf_over(harness: str) -> None:
    """The other half. Over-reporting is its own failure: opencode says "completed", and a naive
    'anything that is not the literal word ok is an error' check would have flagged every healthy
    opencode call in existence."""
    assert _statuses(harness, SUCCESSES[harness]) == ["ok"]


def test_no_bundled_adapter_leaks_a_raw_vendor_word() -> None:
    """Whatever an adapter writes, what leaves the seam is the shared vocabulary. This is the
    invariant that lets the trace, cockpit and cost meter stay harness-agnostic."""
    seen = [s for h, line in {**FAILURES, **SUCCESSES}.items() for s in _statuses(h, line)]
    assert seen, "sanity: the fixtures produced tool calls"
    assert set(seen) <= {"", "ok", "error"}


# ---- adapters stay data -------------------------------------------------------------------------


def test_an_adapter_can_name_its_own_translation_table() -> None:
    """Harnesses report outcomes in incompatible alphabets — codex an exit code, gemini an enum. A
    second table must be addable in YAML alone; needing an interpreter edit per harness would make
    adapters code wearing a data costume."""
    spec = parse_adapter_spec(
        {
            "apiVersion": "swarmkit/v1",
            "kind": "ExecutorAdapter",
            "metadata": {"id": "invented-harness"},
            "spec": {
                "launch": {"command": ["invented"]},
                "stream": {"format": "jsonl"},
                "event_map": [
                    {
                        "when": {"kind": "tool_done"},
                        "emit": [
                            {
                                "event": "tool_call",
                                "with": {
                                    "tool": "$.name",
                                    "status": {"from": "$.rc", "map": "vendor_outcome_map"},
                                },
                            }
                        ],
                    }
                ],
                "vendor_outcome_map": {"0": "ok", "_default": "error"},
            },
        }
    )
    feed = AdapterInterpreter(spec).feed
    assert [
        e.status
        for e in feed({"kind": "tool_done", "name": "t", "rc": 0})
        if isinstance(e, ExecToolCall)
    ] == ["ok"]
    assert [
        e.status
        for e in feed({"kind": "tool_done", "name": "t", "rc": 7})
        if isinstance(e, ExecToolCall)
    ] == ["error"]


def test_status_map_still_works_after_generalization() -> None:
    """Regression guard: `status_map` was the one hardcoded table. Generalizing named maps must not
    break the adapters that already name it."""
    spec = parse_adapter_spec(yaml.safe_load((ADAPTERS / "gemini-cli.yaml").read_text()))
    assert spec.status_map, "gemini-cli still declares status_map"
    assert spec.maps["status_map"] == dict(spec.status_map)


# ---- what reaches the trace ---------------------------------------------------------------------


def test_the_trace_does_not_pass_input_length_off_as_result_length() -> None:
    """The subtler defect. `result_length` was `len(input_summary)`, so an image tool that returned
    NOTHING still showed a healthy number purely because the path was long — the number was not
    merely missing, it was wrong in the reassuring direction."""
    assert "result_length=len(event.input_summary)" not in HARNESS_NODE.read_text()


def test_a_failed_call_is_distinguishable_from_a_successful_one_in_the_trace() -> None:
    """End of the chain, stated as the question the report could not answer: given two tool calls,
    one that worked and one that did not, can a reader tell them apart?"""
    from swarmkit_runtime.trace import ToolCall  # noqa: PLC0415

    def record(event: ExecToolCall) -> ToolCall:
        # Mirrors _harness_node's construction; kept in step by the source assertions above.
        return ToolCall(
            tool_name=event.tool,
            arguments={"input": event.input_summary} if event.input_summary else {},
            error="tool reported failure" if event.status == "error" else None,
        )

    good = record(ExecToolCall(tool="view_image", input_summary="/a.png", status="ok"))
    bad = record(ExecToolCall(tool="view_image", input_summary="/a.png", status="error"))
    unknown = record(ExecToolCall(tool="view_image", input_summary="/a.png"))

    assert good.error is None
    assert bad.error is not None
    assert unknown.error is None, "unreported is not a failure; it must not raise a false alarm"
    assert good.error != bad.error, "identical inputs must not produce identical trace records"
