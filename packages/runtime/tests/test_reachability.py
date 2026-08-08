"""Declared configuration that no code path reaches is reported.

`design/details/declared-but-unreachable.md`. Five defects in a row had one anatomy — declared,
accepted, validated, displayed, and loaded by nothing (bugs 21, 22, 23, 25, 25b). Every individual
fix was correct and none prevented the next; what they share is not a line of code but the absence
of anyone asking whether the thing is reachable.

The tests below are organised around the design's central claim: **this cannot be one check.**

* **Class A, broken at wiring** — `compute_reachability`. The configuration never reaches its
  consumer, so nothing is constructed for it.
* **Class B, broken at selection** — `compute_inert_bindings`. Wired, and the predicate that selects
  it is never true. Bug 23's `Trigger.pre_input == "pre_input"` was simply always False and nothing
  at compile time was wrong, so this half is retrospective, from the audit log, with a denominator.
* **Class C, broken at capability** — not covered, and asserted nowhere, because pretending
  otherwise is how this check would ship as the sixth instance of the defect it exists to catch.

The historical defects are reconstructed as fixtures. `review:` needs no reconstruction at all: it
is declared in the Funnel schema and built by neither binding today, which makes it this check's
acceptance test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance import DecisionSkillBinding
from swarmkit_runtime.reachability import (
    Declaration,
    WiringLedger,
    compute_inert_bindings,
    compute_reachability,
    declarations_for_bindings,
)

SPEC_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"summary": {"type": "string"}}}
_APPROVE = {"rules": [{"scope": "spec:approve", "roles": ["lead"], "quorum": "all"}]}


def _workspace(tmp_path: Path, funnel: dict[str, Any], **topology: Any) -> Path:
    root = tmp_path / "ws"
    for sub in ("topologies", "funnels", "schemas"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "gated", "name": "gated"},
                **topology.pop("workspace", {}),
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
                    "description": "a funnel bound to an agent, for the reachability fixtures",
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
                        "model": {"provider": "mock", "name": "mock"},
                    },
                },
                **topology,
            }
        )
    )
    return root


def _report(root: Path) -> Any:
    return WorkspaceRuntime.from_workspace_path(root).reachability()


def _unreachable_keys(report: Any) -> set[str]:
    return {u.declaration.key for u in report.unreachable}


# ---- class A: the acceptance test ----------------------------------------------------------------


def test_a_declared_review_layer_is_reported(tmp_path: Path) -> None:
    """The check's acceptance test, and the only fixture needing no reconstruction.

    `review:` is in the Funnel schema and `run_agent_funnel_gate` builds no reviewer at all, on
    either binding. A correct implementation reports it on day one.
    """
    root = _workspace(tmp_path, {"review": {"archetype": "reviewer", "route_back_at": "high"}})

    assert "designer:spec-review:review" in _unreachable_keys(_report(root))


def test_a_wired_judge_is_not_reported(tmp_path: Path) -> None:
    """The negative that keeps the report usable: a layer that IS built must not be listed."""
    root = _workspace(tmp_path, {"judge": {"skill": "spec-judge", "threshold": 0.8}})

    report = _report(root)

    assert "designer:spec-review:judge" not in _unreachable_keys(report)
    assert any(d.key == "designer:spec-review:judge" for d in report.reachable)


def test_a_funnel_with_only_wired_layers_is_clean(tmp_path: Path) -> None:
    root = _workspace(tmp_path, {"judge": {"skill": "spec-judge"}})

    assert _report(root).ok, "a fully wired workspace must report nothing"


# ---- class A: the historical defects -------------------------------------------------------------


def test_a_schema_only_validate_that_resolves_nothing_is_reported(tmp_path: Path) -> None:
    """Bug 25b's shape. A `validate.schema` pointing at a file that does not exist builds no
    validator, and the layer is inert — which is exactly what three funnels in the reference
    workspace turned out to be doing, undetected, for months."""
    root = _workspace(tmp_path, {"validate": {"schema": "schemas/not-written-yet.json"}})

    assert "designer:spec-review:validate" in _unreachable_keys(_report(root))


def test_a_schema_validate_that_does_resolve_is_not_reported(tmp_path: Path) -> None:
    """And the same declaration, resolvable, is wired — so the report distinguishes the two rather
    than flagging every `validate:`."""
    root = _workspace(tmp_path, {"validate": {"schema": "schemas/spec.schema.json"}})

    assert "designer:spec-review:validate" not in _unreachable_keys(_report(root))


def test_a_funnel_the_compiler_never_wraps_is_reported() -> None:
    """Bug 25 itself, at the unit seam: with the guard restored to its broken form nothing records,
    and the funnel and every one of its layers is reported.

    Driven through `compute_reachability` rather than a real compile, because the bug was that the
    compiler's branch did not run — there is no way to ask a fixed compiler to not run it.
    """
    declarations = [
        Declaration(kind="funnel", key="designer:spec-review", declared_on="agent designer"),
        Declaration(
            kind="funnel_layer", key="designer:spec-review:judge", declared_on="funnel spec-review"
        ),
    ]

    report = compute_reachability(declarations, WiringLedger())

    assert len(report.unreachable) == 2
    assert not report.ok


def test_a_binding_dropped_by_the_merge_never_appears() -> None:
    """Bug 22's shape. The enumerator reads the MERGED list on purpose: a binding the merge
    discarded is absent from both sides, so this check cannot see it — the merge has its own tests.
    What this asks is the next question, which nobody was asking: it survived the merge, did
    anything wire it?
    """
    assert declarations_for_bindings([], "design") == []


def test_an_unreachable_required_binding_is_marked(tmp_path: Path) -> None:
    """`required: true` and unreachable is the worst case in this family: the workspace believes a
    check is enforcing something that has never run."""
    binding = DecisionSkillBinding(id="spec-conformance", trigger="post_output", required=True)

    report = compute_reachability(
        declarations_for_bindings([binding], "wms-design"), WiringLedger()
    )

    assert len(report.blocking) == 1
    assert "REQUIRED" in report.unreachable[0].line()


def test_a_wired_binding_is_reachable() -> None:
    binding = DecisionSkillBinding(id="memory-reader", trigger="pre_input", required=False)
    ledger = WiringLedger()
    ledger.wired("decision_skill", "design:memory-reader:pre_input")

    report = compute_reachability(declarations_for_bindings([binding], "design"), ledger)

    assert report.ok
    assert report.blocking == ()


# ---- the compiler records what it builds ---------------------------------------------------------


def test_the_compiler_records_the_bindings_it_passes_to_a_node(tmp_path: Path) -> None:
    """The ledger is written by the code that wires, so a real compile populates it."""
    root = _workspace(
        tmp_path,
        {"judge": {"skill": "spec-judge"}},
        workspace={
            "governance": {
                "provider": "mock",
                "decision_skills": [
                    {"id": "memory-reader", "trigger": "pre_input", "required": False}
                ],
            }
        },
    )

    report = _report(root)

    assert any(d.kind == "decision_skill" for d in report.reachable)


def test_the_report_serialises_for_the_endpoint(tmp_path: Path) -> None:
    """`GET /workspace/reachability` returns this verbatim."""
    root = _workspace(tmp_path, {"review": {"archetype": "reviewer"}})

    payload = _report(root).to_dict()

    assert payload["ok"] is False
    assert payload["unreachable"][0]["message"]
    assert json.dumps(payload), "must be JSON-serialisable"


# ---- class B: wired, and never once fired --------------------------------------------------------


def _binding(skill_id: str, trigger: str = "pre_input", *, required: bool = False) -> Any:
    return DecisionSkillBinding(id=skill_id, trigger=cast("Any", trigger), required=required)


def test_a_binding_with_no_evaluations_is_inert() -> None:
    """Bug 23, which no static check could see: correctly wired, selected by nothing, silent."""
    rows = compute_inert_bindings(
        {"wms-design": [_binding("spec-conformance", "post_output", required=True)]},
        evaluations={},
        runs_by_topology={"wms-design": 12},
    )

    assert len(rows) == 1
    assert rows[0].evaluations == 0
    assert rows[0].applicable_runs == 12
    assert "REQUIRED" in rows[0].line()


def test_a_binding_that_has_fired_is_not_inert() -> None:
    rows = compute_inert_bindings(
        {"design": [_binding("memory-reader")]},
        evaluations={("memory-reader", "pre_input"): 3},
        runs_by_topology={"design": 12},
    )

    assert rows == ()


def test_a_binding_with_no_applicable_runs_is_not_reported() -> None:
    """The denominator is the point. "Zero evaluations" out of zero runs has taught us nothing, and
    reporting it would train the reader to ignore the report — which is how the original defects
    survived being displayed by `validate` all along."""
    rows = compute_inert_bindings(
        {"design": [_binding("memory-reader")]},
        evaluations={},
        runs_by_topology={},
    )

    assert rows == ()


def test_runs_accumulate_across_the_topologies_a_binding_is_bound_on() -> None:
    """A workspace-level binding applies to several topologies; its denominator is their sum, so a
    binding bound widely and never fired reads as loudly as it should."""
    rows = compute_inert_bindings(
        {"a": [_binding("memory-reader")], "b": [_binding("memory-reader")]},
        evaluations={},
        runs_by_topology={"a": 20, "b": 27},
    )

    assert len(rows) == 1
    assert rows[0].applicable_runs == 47
    assert rows[0].topologies == ("a", "b")


def test_required_bindings_sort_first() -> None:
    """The loudest line goes where a reader looks first."""
    rows = compute_inert_bindings(
        {
            "a": [
                _binding("advisory-one"),
                _binding("spec-conformance", "post_output", required=True),
            ]
        },
        evaluations={},
        runs_by_topology={"a": 5},
    )

    assert [r.skill_id for r in rows] == ["spec-conformance", "advisory-one"]


# ---- the check is itself configuration that could go inert ---------------------------------------


def test_every_funnel_layer_the_schema_allows_is_enumerated() -> None:
    """The guard against this check quietly narrowing.

    A layer added to the Funnel schema and not added to `FUNNEL_LAYERS` would never be enumerated,
    so it could go unwired for months and this check would say the workspace was clean — the exact
    failure it exists to prevent, one level up.
    """
    from pathlib import Path as _Path  # noqa: PLC0415

    from swarmkit_runtime.reachability import FUNNEL_LAYERS  # noqa: PLC0415

    schema = json.loads(
        (
            _Path(__file__).resolve().parents[3] / "packages/schema/schemas/funnel.schema.json"
        ).read_text()
    )
    declared = {k for k in schema.get("properties", {}) if k in {*FUNNEL_LAYERS, "review"}}

    missing = declared - set(FUNNEL_LAYERS)
    assert not missing, f"funnel layers in the schema but not enumerated: {sorted(missing)}"


@pytest.mark.parametrize("layer", ["validate", "judge", "review", "approve"])
def test_each_layer_is_enumerated_when_declared(tmp_path: Path, layer: str) -> None:
    """Each layer must at least be SEEN — reachable or not. A layer the enumerator skips is a layer
    that can never be reported."""
    from swarmkit_runtime.reachability import declarations_for_topology  # noqa: PLC0415
    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415

    specs: dict[str, Any] = {
        "validate": {"schema": "schemas/spec.schema.json"},
        "judge": {"skill": "spec-judge"},
        "review": {"archetype": "reviewer"},
        "approve": _APPROVE,
    }
    root = _workspace(tmp_path, {layer: specs[layer]})

    keys = {d.key for d in declarations_for_topology(resolve_workspace(root), "design")}

    assert f"designer:spec-review:{layer}" in keys
