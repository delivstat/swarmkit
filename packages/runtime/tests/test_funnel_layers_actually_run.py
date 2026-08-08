"""A funnel bound to an agent actually runs its layers on the path serve and the CLI use.

`compile_topology` wrapped an agent's node in its funnel gate only when `review_queue is not None`,
and **nothing in the runtime ever passed one**. `WorkspaceRuntime.compile()` — the entry point serve
and the CLI both go through — passed ten kwargs and neither `review_queue` nor `role_registry` was
among them, so the guard was False on every run either has ever made. A declared funnel was
resolved, attached, validated and then inert.

The guard was also on the wrong dependency. The review queue is read by exactly one thing in
`_gate_funnel`: the multi-party approver. `validate` needs nothing and `judge` needs only
governance. Three layers that touch no queue were gated behind the one that does.

And `validate: {schema: ...}` was a second hole behind the first. `build_deterministic_validator`
returned None for a schema-only validate, on the stated grounds that it "stays handled by output
governance" — which it was not: `output_schema` is merged from the agent and its archetype only,
never from the funnel, and nothing bridged them. Three consecutive specs shipped with `code_changes`
entries whose `kind` and `action` are not in their own schema's enums, read and approved by a human
against a contract nothing had enforced.

**Human approval is deliberately not on this path.** `approve` is the sole predecessor of END — the
invariant that stops an advisory layer deciding — so a gate that runs needs an approver, and in-node
there is nothing to park a human in: `resolve_multiparty` would poll the review queue inside the
agent's coroutine for up to seven days (`_DEFAULT_MAX_WAIT_SECONDS`), hold the model session, and
lose the wait on a serve restart. A `swarmkit run` from a terminal could not approve at all. Human
approval on the pipeline path is the stage-level `gate:`, which parks the saga durably.

So the in-node approver records and passes, and every run says so in the audit log. It is stated
there rather than in a warning because `approve` is a REQUIRED property of the Funnel schema — every
funnel has one, so a warning would fire on every compile of every gated topology and mean nothing.
The audit record survives log levels and can be queried after the fact.

These tests drive `WorkspaceRuntime.compile()` rather than `compile_topology` directly, because the
missing kwarg WAS the bug: every existing funnel test passed while the feature was unreachable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance import DecisionSkillResult
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.model_providers import (
    CompletionResponse,
    ContentBlock,
    MockModelProvider,
    ProviderRegistry,
    Usage,
)
from swarmkit_runtime.resolver import resolve_workspace

pytestmark = pytest.mark.asyncio

SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "code_changes"],
    "properties": {
        "summary": {"type": "string"},
        "code_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "action"],
                "properties": {
                    "kind": {"enum": ["code", "schema"]},
                    "action": {"enum": ["add", "remove"]},
                },
            },
        },
    },
}

CONFORMING = json.dumps(
    {"summary": "ok", "code_changes": [{"kind": "code", "action": "add"}]},
)
#: the shape the reporter's three specs actually had: `config` and `modify` are not in the enums.
VIOLATING = json.dumps(
    {"summary": "ok", "code_changes": [{"kind": "config", "action": "modify"}]},
)


#: `approve` is a required property of the Funnel schema, so every fixture carries one — which is
#: exactly why it cannot be the thing that decides whether the gate runs in-node.
_APPROVE = {"rules": [{"scope": "spec:approve", "roles": ["lead"], "quorum": "all"}]}


def _workspace(tmp_path: Path, funnel: dict[str, Any]) -> Path:
    """The reproduction as a workspace on disk: an agent with `funnel:` and nothing else unusual."""
    root = tmp_path / "ws"
    for sub in ("topologies", "funnels", "archetypes", "schemas"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "gated", "name": "gated"},
            }
        )
    )
    (root / "schemas" / "spec.schema.json").write_text(json.dumps(SPEC_SCHEMA))
    (root / "funnels" / "spec-review.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Funnel",
                "metadata": {
                    "id": "spec-review",
                    "name": "Spec Review",
                    "description": "the reporter's funnel: validate + judge, bound to an agent",
                },
                "approve": _APPROVE,
                "provenance": {"authored_by": "human", "version": "1.0.0"},
                **funnel,
            }
        )
    )
    (root / "topologies" / "design.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Topology",
                "metadata": {"name": "design", "version": "0.1.0"},
                "agents": {
                    "root": {
                        "id": "designer",
                        "role": "root",
                        "funnel": "spec-review",
                        # Without an explicit provider the compiler falls back to a default
                        # MockModelProvider and the scripted outputs below never reach the gate.
                        "model": {"provider": "mock", "name": "mock"},
                    },
                },
            }
        )
    )
    return root


class _Governance(MockGovernanceProvider):
    """Records every decision-skill call, and answers the judge with a scripted verdict."""

    def __init__(self, verdicts: list[tuple[str, float]] | None = None) -> None:
        super().__init__(allow_all=True)
        self.asked: list[str] = []
        self.seen: list[str] = []
        self._verdicts = list(verdicts or [])

    async def evaluate_decision_skill(self, *, skill_id: str, **_kw: Any) -> DecisionSkillResult:
        self.asked.append(skill_id)
        verdict, confidence = self._verdicts.pop(0) if self._verdicts else ("pass", 1.0)
        return DecisionSkillResult(
            skill_id=skill_id,
            verdict=cast("Any", verdict),
            confidence=confidence,
            reasoning="scripted",
        )

    async def record_event(self, event: Any) -> None:
        self.seen.append(event.event_type)
        await super().record_event(event)


def _runtime(root: Path, governance: Any, outputs: list[str]) -> WorkspaceRuntime:
    """A runtime whose agent emits `outputs` in order — one per draft/revision."""
    remaining = list(outputs)

    class _Provider(MockModelProvider):
        async def complete(self, request: Any) -> CompletionResponse:
            text = remaining.pop(0) if remaining else outputs[-1]
            return CompletionResponse(
                content=(ContentBlock(type="text", text=text),),
                stop_reason="end_turn",
                usage=Usage(),
            )

    registry = ProviderRegistry()
    registry.register(_Provider())
    return WorkspaceRuntime(
        workspace=resolve_workspace(root),
        workspace_root=root,
        provider_registry=registry,
        governance=governance,
        mcp_manager=None,
    )


async def _run(runtime: WorkspaceRuntime, text: str = "draft the spec") -> dict[str, Any]:
    graph = runtime.compile("design")
    return dict(await graph.ainvoke({"input": text, "agent_results": {}}))


# ---- the judge layer runs -----------------------------------------------------------------------


async def test_the_judge_layer_is_actually_evaluated(tmp_path: Path) -> None:
    """The reported reproduction: `grep -c 'spec-judge'` was 0 on a $1.82 design stage."""
    root = _workspace(tmp_path, {"judge": {"skill": "spec-judge", "threshold": 0.75}})
    gov = _Governance()

    await _run(_runtime(root, gov, [CONFORMING]))

    assert "spec-judge" in gov.asked, "the funnel's judge skill must reach governance"


async def test_a_failing_judge_sends_the_draft_back(tmp_path: Path) -> None:
    """The bounded retry is the point of the judge: it rewrites BEFORE a human is asked to read.

    That loop had never executed, so every spec defect was caught by a person, or not at all.
    """
    root = _workspace(
        tmp_path,
        {"judge": {"skill": "spec-judge", "threshold": 0.75, "max_retries": 1}},
    )
    gov = _Governance([("fail", 0.2), ("pass", 0.9)])

    await _run(_runtime(root, gov, [VIOLATING, CONFORMING]))

    assert gov.asked == ["spec-judge", "spec-judge"], "a failing judge must trigger one revision"


async def test_a_judge_below_threshold_fails_on_confidence(tmp_path: Path) -> None:
    """`pass` is not enough — the threshold is what the layer is configured with."""
    root = _workspace(
        tmp_path,
        {"judge": {"skill": "spec-judge", "threshold": 0.9, "max_retries": 1}},
    )
    gov = _Governance([("pass", 0.5), ("pass", 0.95)])

    await _run(_runtime(root, gov, [CONFORMING, CONFORMING]))

    assert len(gov.asked) == 2


# ---- the schema validate layer runs --------------------------------------------------------------


async def test_a_schema_violating_artifact_is_sent_back(tmp_path: Path) -> None:
    """The layer that would have caught the three shipped specs. A schema-only `validate` wired no
    node at all, on the incorrect grounds that output governance covered it."""
    root = _workspace(
        tmp_path,
        {
            "validate": {"schema": "../schemas/spec.schema.json"},
            "judge": {"skill": "spec-judge", "threshold": 0.75, "max_retries": 1},
        },
    )
    gov = _Governance()
    captured: list[dict[str, Any]] = []

    async def _record(event: Any) -> None:
        if event.event_type == "funnel.advisory_completed":
            captured.append(dict(event.payload))

    gov.record_event = _record  # type: ignore[method-assign]

    result = await _run(_runtime(root, gov, [VIOLATING, CONFORMING]))

    assert captured and captured[0]["retries"] == 1, "the violating draft must be sent back once"
    assert captured[0]["failed_layers"] == [], "and the rewrite must then clear validate"
    assert "config" not in str(result.get("agent_results", {})), "the rewrite is what survives"


async def test_a_conforming_artifact_passes_validate_untouched(tmp_path: Path) -> None:
    root = _workspace(
        tmp_path,
        {
            "validate": {"schema": "../schemas/spec.schema.json"},
            "judge": {"skill": "spec-judge", "threshold": 0.75},
        },
    )
    gov = _Governance()

    await _run(_runtime(root, gov, [CONFORMING]))

    assert len(gov.asked) == 1, "a conforming artifact must not be revised"


async def test_fenced_json_is_not_a_schema_failure(tmp_path: Path) -> None:
    """A model that fences its JSON produced a conforming artifact badly presented. Failing it as
    "not JSON" would send the drafter to fix the wrong thing."""
    root = _workspace(tmp_path, {"validate": {"schema": "../schemas/spec.schema.json"}})
    gov = _Governance()

    result = await _run(_runtime(root, gov, [f"Here you go:\n```json\n{CONFORMING}\n```"]))

    assert "GATE REJECTED" not in str(result.get("agent_results", {}))


# ---- the artifact still proceeds -----------------------------------------------------------------


async def test_retry_exhaustion_proceeds_with_the_failure_recorded(tmp_path: Path) -> None:
    """A declared contract the output violates is worth a rewrite and a record — not a failed run.
    Turning every currently-passing pipeline into a failing one is not what a quality gate is for.
    """
    root = _workspace(
        tmp_path,
        {"validate": {"schema": "../schemas/spec.schema.json"}, "judge": {"skill": "j"}},
    )
    gov = _Governance()

    result = await _run(_runtime(root, gov, [VIOLATING]))

    assert "GATE REJECTED" not in str(result.get("agent_results", {}))
    assert "funnel.advisory_completed" in gov.seen, "the failure must reach the audit log"


async def test_the_audit_record_names_the_failed_layer(tmp_path: Path) -> None:
    """ "It ran and something failed" is only useful if the record says WHICH layer."""
    root = _workspace(tmp_path, {"validate": {"schema": "../schemas/spec.schema.json"}})
    gov = _Governance()
    captured: list[dict[str, Any]] = []

    async def _record(event: Any) -> None:
        if event.event_type == "funnel.advisory_completed":
            captured.append(dict(event.payload))

    gov.record_event = _record  # type: ignore[method-assign]

    await _run(_runtime(root, gov, [VIOLATING]))

    assert captured and captured[0]["failed_layers"] == ["validate"]


# ---- and approve does not block ------------------------------------------------------------------


async def test_a_declared_approve_layer_does_not_wait_for_a_human(tmp_path: Path) -> None:
    """Wiring the deps as first suggested would poll the review queue in-node for up to 7 days.

    This test completing at all is the assertion.
    """
    root = _workspace(
        tmp_path,
        {"judge": {"skill": "spec-judge"}},
    )
    gov = _Governance()

    result = await _run(_runtime(root, gov, [CONFORMING]))

    assert "GATE REJECTED" not in str(result.get("agent_results", {}))


async def test_every_run_records_that_approve_was_deferred(tmp_path: Path) -> None:
    """Never silently — and stated where it survives log levels.

    A reader of the audit log must never have to infer why a declared approve layer produced no
    approval event. This is the durable half of that guarantee; the compile-time notice is INFO,
    because `approve` is mandatory in the schema and a warning on every gated compile is noise.
    """
    root = _workspace(tmp_path, {"judge": {"skill": "spec-judge"}})
    gov = _Governance()
    captured: list[dict[str, Any]] = []

    async def _record(event: Any) -> None:
        if event.event_type == "funnel.advisory_completed":
            captured.append(dict(event.payload))

    gov.record_event = _record  # type: ignore[method-assign]

    await _run(_runtime(root, gov, [CONFORMING]))

    assert captured and captured[0]["approve"] == "deferred to the stage gate"


async def test_the_compile_notice_names_the_funnel_and_the_remedy(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _workspace(tmp_path, {"judge": {"skill": "spec-judge"}})

    with caplog.at_level("INFO", logger="swarmkit.funnels"):
        _runtime(root, _Governance(), [CONFORMING]).compile("design")

    assert "spec-review" in caplog.text
    assert "stage-level `gate:`" in caplog.text


# ---- an unusable schema does not take the funnel down --------------------------------------------


async def test_an_unreadable_schema_warns_and_the_funnel_still_runs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A configuration error the operator has to see — not a validate layer that disappears and not
    a run that dies."""
    root = _workspace(
        tmp_path,
        {"validate": {"schema": "../schemas/not-here.json"}, "judge": {"skill": "spec-judge"}},
    )
    gov = _Governance()

    with caplog.at_level("WARNING"):
        await _run(_runtime(root, gov, [CONFORMING]))

    assert "not-here.json" in caplog.text
    assert gov.asked == ["spec-judge"], "the rest of the funnel must still run"
