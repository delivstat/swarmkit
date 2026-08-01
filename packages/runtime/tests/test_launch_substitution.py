"""Launch-template substitution must not corrupt the values it inserts.

This is the cause of "bug 5" in the wms-support report: an orchestrator-driven stage that died in
~1s at $0.00 with `no result event`. `_sub` substituted values in, then re-scanned the *result* for
leftover braces — so braces arriving as part of a VALUE were mistaken for unresolved placeholders
and everything between them was deleted.

With the claude-code template (`claude -p {task.statement}`) the value is the entire prompt:

- a JSON statement emptied completely, and `claude -p` refused with
  "Input must be provided either through stdin or as a prompt argument";
- any prompt containing code, a stack trace, or a log line was SILENTLY truncated, and the agent
  answered a question nobody asked.

Every prior test used a brace-free statement, which is why a full suite never saw it. The
`agent.role` fallback (removed in 1.127.0) masked it further: an emptied statement fell back to the
literal "root", which is brace-free and non-empty, so the harness ran and reported success.
"""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_runtime.executors._adapter_spec import parse_adapter_spec
from swarmkit_runtime.executors._event_map import _sub, build_command

JSON_STATEMENT = '{"ticket_id": "WMS-1", "title": "t", "input": "do the thing"}'


# ---- the values that used to be destroyed -------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("Reply with READY and nothing else.", id="brace-free (always worked)"),
        pytest.param("Reply with READY {foo} and nothing else.", id="braced span mid-prompt"),
        pytest.param(JSON_STATEMENT, id="json envelope (emptied to 0 chars)"),
        pytest.param("Fix this: if (x) { doThing(); } and report back.", id="code in a prompt"),
        pytest.param("stack: at Foo.bar(Foo.java:12) {cause}", id="stack trace"),
        pytest.param("yaml: {a: 1, b: [2, 3]}", id="yaml flow style"),
        pytest.param("{}", id="empty braces"),
        pytest.param("}{", id="reversed braces"),
        pytest.param('{"nested": {"deep": true}}', id="nested json"),
    ],
)
def test_a_value_reaches_the_command_unchanged(statement: str) -> None:
    assert _sub("{task.statement}", {"task.statement": statement}) == statement


def test_a_value_is_not_rescanned_for_placeholders() -> None:
    """The precise defect: a value that *looks* like a placeholder is still a value."""
    assert _sub("{a}", {"a": "{b}", "b": "SHOULD-NOT-APPEAR"}) == "{b}"


# ---- the two documented behaviours, which must survive the fix ---------------------------------


def test_known_placeholder_substitutes() -> None:
    assert _sub("a {x} b", {"x": "V"}) == "a V b"


def test_unknown_placeholder_collapses_to_empty() -> None:
    assert _sub("a {nope} b", {}) == "a  b"


def test_multiple_placeholders_in_one_template() -> None:
    assert _sub("{a}-{b}-{c}", {"a": "1", "b": "2"}) == "1-2-"


def test_a_stray_brace_no_longer_eats_the_rest_of_the_template() -> None:
    """The old loop deleted from the first `{` to the next `}` regardless of what lay between."""
    assert _sub("keep { this } text", {}) == "keep { this } text"


# ---- through the real command builder ----------------------------------------------------------

ADAPTER: dict[str, Any] = {
    "apiVersion": "swarmkit/v1",
    "kind": "ExecutorAdapter",
    "metadata": {"id": "fake", "name": "Fake", "description": "test adapter"},
    "spec": {
        "launch": {"command": ["claude", "-p", "{task.statement}", "--output-format", "json"]},
        "event_map": [{"when": {"type": "result"}, "emit": [{"event": "result"}]}],
    },
    "provenance": {"authored_by": "human", "version": "1.0.0"},
}


def test_build_command_passes_a_json_statement_through_intact() -> None:
    """End-to-end at the seam that actually launches the harness: the argv element `claude -p`
    receives must be the statement, not an empty string."""
    argv = build_command(parse_adapter_spec(ADAPTER), {"task.statement": JSON_STATEMENT})
    assert JSON_STATEMENT in argv
    assert "" not in argv, "an emptied statement is what made `claude -p` refuse the run"


def test_build_command_keeps_code_braces() -> None:
    stmt = "Review: public void f() { return; }"
    argv = build_command(parse_adapter_spec(ADAPTER), {"task.statement": stmt})
    assert stmt in argv
