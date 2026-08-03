"""Demo: a failed harness tool call is visible in the trace — for every harness.

The bug: a design agent described UI screens it had never seen, because the image tool was handed
paths that resolved nowhere and returned nothing. The trace showed `view-screenshot ✓` either way.

This feeds one FAILING tool call per bundled harness, in that harness's own native protocol, and
shows what the trace records before and after the fix.

    uv run python packages/runtime/demos/harness_tool_outcomes.py
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml
from swarmkit_runtime.executors._adapter_spec import parse_adapter_spec
from swarmkit_runtime.executors._event_map import AdapterInterpreter
from swarmkit_runtime.executors._events import ExecToolCall
from swarmkit_runtime.trace import ToolCall

ADAPTERS = pathlib.Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/executors/adapters"

# Each harness reports failure on a different event, in a different alphabet.
FAILING_CALLS: dict[str, tuple[str, dict[str, Any]]] = {
    "claude-code": (
        "is_error on a following tool_result",
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "is_error": True,
                        "name": "view_image",
                        "content": "ENOENT: ./screens/login.png",
                    }
                ]
            },
        },
    ),
    "codex": (
        "a non-zero exit code on exec_command_end",
        {"msg": {"type": "exec_command_end", "exit_code": 1, "stdout": ""}},
    ),
    "gemini-cli": (
        "status: error on tool_call_response",
        {
            "type": "tool_call_response",
            "name": "read_file",
            "status": "error",
            "response": "ENOENT",
        },
    ),
    "opencode": (
        "state.status on the tool event itself",
        {"type": "tool", "part": {"tool": "read", "state": {"status": "error", "input": "x.png"}}},
    ),
}

# opencode is the interesting success case: it says "completed", not "ok".
SUCCEEDING_CALL = (
    "opencode",
    {"type": "tool", "part": {"tool": "read", "state": {"status": "completed", "input": "x.png"}}},
)


def _tool_calls(harness: str, line: dict[str, Any]) -> list[ExecToolCall]:
    spec = parse_adapter_spec(yaml.safe_load((ADAPTERS / f"{harness}.yaml").read_text()))
    return [e for e in AdapterInterpreter(spec).feed(line) if isinstance(e, ExecToolCall)]


def _before(event: ExecToolCall) -> ToolCall:
    """What the trace used to record: no outcome, and the ARGUMENT length passed off as the result
    length — so a tool that returned nothing still showed a healthy number."""
    return ToolCall(tool_name=event.tool, result_length=len(event.input_summary))


def _after(event: ExecToolCall) -> ToolCall:
    return ToolCall(
        tool_name=event.tool,
        arguments={"input": event.input_summary} if event.input_summary else {},
        error="tool reported failure" if event.status == "error" else None,
    )


def _render(tc: ToolCall) -> str:
    return f"{tc.tool_name} {'✗ ' + (tc.error or '') if tc.error else '✓'}"


def main() -> None:
    print("\n  A FAILING tool call, per harness\n  " + "─" * 68)
    print(f"  {'harness':<13} {'how it reports failure':<38} {'before':<10} after")
    print("  " + "─" * 68)
    for harness, (how, line) in FAILING_CALLS.items():
        calls = _tool_calls(harness, line)
        if not calls:
            print(f"  {harness:<13} {how:<38} {'—':<10} NOT MAPPED")
            continue
        for event in calls:
            before, after = _render(_before(event)), _render(_after(event))
            print(f"  {harness:<13} {how:<38} {before:<10} {after}")

    harness, line = SUCCEEDING_CALL
    print("\n  A SUCCEEDING call — the over-reporting guard\n  " + "─" * 68)
    for event in _tool_calls(harness, line):
        print(
            f"  {harness} says {'state.status=completed':<30} normalized={event.status!r}"
            f"  ->  {_render(_after(event))}"
        )
    print(
        "\n  'completed' is not the literal word 'ok'. A naive check would have flagged every\n"
        "  healthy opencode call as a failure — which is why the vocabulary is normalized once,\n"
        "  at the seam, instead of per adapter.\n"
    )


if __name__ == "__main__":
    main()
