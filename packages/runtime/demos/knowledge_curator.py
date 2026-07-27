"""Demo: the knowledge-curator reference topology + the memory-reconcile decision skill
(design/details/governed-memory.md).

Proves the whole governed-memory stack end to end WITHOUT an LLM: a scripted skill output flows
through the real `_parse_result` (so `raw` carries the reconcile op), `build_memory_reconciler`, and
`GovernedMemoryStore.awrite` — the exact production path minus the model call. Then it resolves +
compiles the knowledge-curator reference topology.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine
from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.governance._decision_evaluator import _parse_result
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.governed_memory import (
    GovernedMemoryStore,
    MemoryCandidate,
    build_memory_reconciler,
)
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers import MockModelProvider, ProviderRegistry
from swarmkit_runtime.resolver import resolve_workspace

REPO_ROOT = Path(__file__).resolve().parents[3]


class _ScriptedGov:
    """Stands in for governance: instead of calling the LLM, it runs the REAL skill-output parser
    over a scripted `memory-reconcile` JSON — the `DecisionSkillResult` production would get."""

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = iter(outputs)

    async def evaluate_decision_skill(self, **_kw: object) -> DecisionSkillResult:
        return _parse_result("memory-reconcile", next(self._outputs))


def _cand(value: str) -> MemoryCandidate:
    return MemoryCandidate(subject="user:srijith", attribute="preferred_tool_model", value=value)


async def _end_to_end() -> None:
    # Scripted skill outputs — what the memory-reconcile LLM would return for each changed value.
    gov = _ScriptedGov(
        [
            '{"op": "refine", "verdict": "needs-revision", "confidence": 0.8, '
            '"merged_value": "Kimi K2.5 (tools), DeepSeek V3 (writing)", "reasoning": "both hold"}',
            '{"op": "contradict", "verdict": "fail", "confidence": 0.9, '
            '"reasoning": "reverses a firmly-held, high-confidence preference"}',
        ]
    )
    reconciler = build_memory_reconciler(
        governance=gov, skill_id="memory-reconcile", agent_id="knowledge-curator"
    )
    store = GovernedMemoryStore(create_engine("sqlite:///:memory:"), reconciler=reconciler)

    print("Governed writes through the real skill-output → reconciler → store path:\n")
    store.write(_cand("Kimi K2.5"))
    print("  seed        'Kimi K2.5'                → new")

    out = await store.awrite(_cand("DeepSeek V3"))  # skill says refine
    m = store.get("user:srijith", "preferred_tool_model")
    assert m is not None
    print(f"  observe     'DeepSeek V3'              → {out.op} → '{m.value}'")

    out2 = await store.awrite(_cand("never use Kimi"))  # skill says contradict
    m2 = store.get("user:srijith", "preferred_tool_model")
    q = store.list_quarantine()
    assert m2 is not None
    print(
        f"  observe     'never use Kimi'           → {out2.op} → quarantined ({len(q)}); "
        f"trusted value still '{m2.value}'"
    )


def _compile_topology() -> None:
    workspace = resolve_workspace(REPO_ROOT / "reference")
    topology = workspace.topologies["knowledge-curator"]
    registry = ProviderRegistry()
    registry.register(MockModelProvider())
    graph = compile_topology(
        topology,
        provider_registry=registry,
        governance=MockGovernanceProvider(allow_all=True),
    )
    children = [c.id for c in topology.root.children]
    print("\nknowledge-curator reference topology:")
    print(f"  root '{topology.root.id}' → {children}   (compiles: {graph is not None})")
    judge = next(c for c in topology.root.children if c.id == "reconcile-judge")
    print(f"  reconcile-judge binds skill: {[s.id for s in judge.skills]}")


async def main() -> None:
    await _end_to_end()
    _compile_topology()
    print("\n✓ scripted skill output drives refine/contradict; topology compiles.")


if __name__ == "__main__":
    asyncio.run(main())
