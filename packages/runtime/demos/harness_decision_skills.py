"""Demo: a `required: true` decision skill now runs on a harness executor.

The bug (wms-design): a topology bound `spec-conformance` with `required: true`. On an agent whose
`executor.kind` is `harness`, the skill was never invoked — `node_fn` returned to the harness runner
before reaching any gate. The agent returned a markdown document where a JSON object was required,
and that markdown became the run's final output, with the run reporting success.

    uv run python packages/runtime/demos/harness_decision_skills.py
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import swarmkit_runtime.langgraph_compiler._harness_node as hn
from swarmkit_runtime.governance import DecisionSkillBinding
from swarmkit_runtime.langgraph_compiler._compiler import _run_harness_with_gates

MARKDOWN = "# WMS Design\n\n## Resources and screens\n\nThe PGM screen confirms a pick…"
JSON_SPEC = '{"screens": [{"id": "PGM", "confirms": "pick"}], "resources": []}'


@dataclass
class _Verdict:
    verdict: str
    skill_id: str = "spec-conformance"
    reasoning: str = "output is markdown; the topology requires a JSON object matching the spec"
    flagged_items: list[str] = field(default_factory=list)


class _Governance:
    """A deterministic stand-in for the real spec-conformance skill: JSON passes, prose fails."""

    def __init__(self) -> None:
        self.judged: list[str] = []

    async def record_event(self, _event: Any) -> None:
        return None

    async def evaluate_decision_skill(self, *, content: str, **_kw: Any) -> _Verdict:
        self.judged.append(content)
        ok = content.strip().startswith("{")
        return _Verdict(verdict="pass" if ok else "fail")


@dataclass
class _Executor:
    kind: str = "harness"
    ref: str = "claude-code"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Agent:
    id: str = "designer"
    role: str = "designer"
    executor: _Executor = field(default_factory=_Executor)
    skills: list[Any] = field(default_factory=list)
    model: dict[str, Any] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)


def _install_harness(outputs: list[str], seen: list[str]) -> None:
    """A fake harness: returns `outputs` in order, recording the statement it was given."""

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


def _snippet(text: str, width: int = 58) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= width else one_line[: width - 1] + "…"


async def main() -> None:
    binding = DecisionSkillBinding(
        id="spec-conformance", trigger="post_output", required=True, config={"max_retries": 2}
    )

    print("\n  topology: designer  executor.kind: harness (claude-code)")
    print("  governance.decision_skills: spec-conformance  trigger: post_output  required: true")
    print("  " + "─" * 76)

    seen: list[str] = []
    _install_harness([MARKDOWN, JSON_SPEC], seen)
    gov = _Governance()

    print("  BEFORE  the gate never ran; this markdown was the run's final output:")
    print(f"          {_snippet(MARKDOWN)}")
    print("          skill invocations: 0     harness runs: 1     run status: success\n")

    result = await _run_harness_with_gates(
        _Agent(),  # type: ignore[arg-type]
        cast("Any", {"input": "Design the RF screens for the pick-confirm flow."}),
        gov,  # type: ignore[arg-type]
        agent_id="designer",
        bindings=[binding],
    )

    print("  AFTER   the gate runs, fails the markdown, and the harness is re-invoked:")
    for i, judged in enumerate(gov.judged, 1):
        status = "pass" if judged.strip().startswith("{") else "FAIL"
        print(f"          judgement {i}: {status}  {_snippet(judged, 46)}")
    print(f"          skill invocations: {len(gov.judged)}     harness runs: {len(seen)}")
    print(f"\n          final output: {_snippet(str(result['output']))}")
    print("  " + "─" * 76)
    print(
        "\n  The retry re-invokes the HARNESS, not a model. A model asked to revise the text\n"
        "  would produce a description of a fix; only the harness can redo the work in its\n"
        "  own sandbox. The second statement carries the reason:\n"
    )
    tail = seen[1].split("requires changes before this can be accepted.")[-1].strip()
    print(f"      …{_snippet(tail, 70)}\n")


if __name__ == "__main__":
    asyncio.run(main())
