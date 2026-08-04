"""Demo: `output_schema` is enforced on a harness executor.

The gap: `_harness_node.py` had zero references to `output_schema`. A harness agent had neither a
schema constraint nor (until 1.142.0) a post-hoc decision-skill check — so wms-design could return a
markdown document where the topology declared a JSON object, and the run reported success.

    uv run python packages/runtime/demos/harness_output_schema.py
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, cast

import swarmkit_runtime.langgraph_compiler._harness_node as hn
from swarmkit_runtime.langgraph_compiler._compiler import _run_harness_with_gates

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["screens", "resources"],
    "properties": {"screens": {"type": "array"}, "resources": {"type": "array"}},
}
MARKDOWN = "# WMS Design\n\n## Resources and screens\n\nThe PGM screen confirms a pick…"
PARTIAL = json.dumps({"screens": [{"id": "PGM"}]})
CONFORMING = json.dumps({"screens": [{"id": "PGM"}], "resources": ["getTaskList"]})


class _Governance:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def record_event(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class _Executor:
    kind: str = "harness"
    ref: str = "claude-code"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Agent:
    id: str = "designer"
    role: str = "worker"
    executor: _Executor = field(default_factory=_Executor)
    skills: list[Any] = field(default_factory=list)
    model: dict[str, Any] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    output_schema_disabled: bool = False


def _install(outputs: list[str], seen: list[str]) -> None:
    async def _run(agent: Any, state: Any, _gov: Any, **_kw: Any) -> dict[str, Any]:
        seen.append(str(state.get("input", "")))
        text = outputs[min(len(seen) - 1, len(outputs) - 1)]
        return {
            "current_agent": agent.id,
            "agent_results": {agent.id: text},
            "messages": [],
            "output": text,
        }

    hn.run_harness_node = _run  # type: ignore[assignment]


def _snip(text: str, width: int = 52) -> str:
    one = " ".join(text.split())
    return one if len(one) <= width else one[: width - 1] + "…"


async def main() -> None:
    print("\n  designer  executor.kind: harness   output_schema: required [screens, resources]")
    print("  " + "─" * 78)
    print("  BEFORE  output_schema was ignored on this path entirely:")
    print(f"          final output: {_snip(MARKDOWN)}")
    print("          validation rounds: 0     run status: success\n")

    seen: list[str] = []
    _install([MARKDOWN, PARTIAL, CONFORMING], seen)
    gov = _Governance()

    result = await _run_harness_with_gates(
        _Agent(output_schema=SCHEMA),  # type: ignore[arg-type]
        cast("Any", {"input": "Design the RF screens for the pick-confirm flow."}),
        cast("Any", gov),
        agent_id="designer",
        bindings=[],
    )

    print("  AFTER   each non-conforming result goes back to the HARNESS with the exact fields:")
    for i, statement in enumerate(seen[1:], 1):
        fields = statement.split("validation errors on these fields:")[-1].strip().splitlines()[0]
        print(f"          correction {i}: {_snip(fields, 60)}")
    print(f"          harness runs: {len(seen)}     final output: {_snip(str(result['output']))}")
    print("  " + "─" * 78)
    print(
        "\n  Only an EXPLICIT schema is enforced here. The model path falls back to the worker\n"
        "  platform default ({findings: […]}) — applying that would force a findings-schema on\n"
        "  every harness worker, including sdlc-pipeline's `developer`, which produces a diff.\n"
    )
    bare = _Agent()
    seen2: list[str] = []
    _install([MARKDOWN], seen2)
    out = await _run_harness_with_gates(
        bare,  # type: ignore[arg-type]
        cast("Any", {"input": "Implement the change."}),
        cast("Any", _Governance()),
        agent_id="designer",
        bindings=[],
    )
    print(
        f"      worker, no declared schema -> harness runs: {len(seen2)}, "
        f"output unchanged: {out['output'] == MARKDOWN}\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
