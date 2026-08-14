"""How strongly an agent's output is checked — the successor to `swarmkit gates`.

`gate_coverage` answered "what is the narrowest verified edge of this pipeline" by classifying stage
edges against their funnels. The stage graph left with the bundled sequencer and the edge analysis
went with it, leaving `swarmkit gates --require` without a successor — the one real regression of
that removal.

The question underneath was never about pipelines. **Which agents produce an artifact, and what
checks it** is about topologies and funnels, both of which stay, and it is the natural sibling of
the reachability report: *"this run's output is verified by nothing"* is the same class of finding
as *"this binding is reached by nothing"*.

Two properties carry the design:

* **Strength counts WIRED layers, not declared ones.** A `validate` whose builder returned None
  contributes nothing because it does nothing, and counting it would make this check repeat the
  exact defect the reachability report exists to catch. Both answers come from one compile and one
  ledger, so they cannot disagree.
* **Only roots are findings.** A leaf worker returning a fact to its parent is not producing a
  reviewable artifact; flagging every agent would make a report nobody reads, which is how the
  thing this replaces would have failed if it had been per-agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.reachability import WiringLedger
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.verification import compute_verification

APPROVE: dict[str, Any] = {
    "rules": [{"scope": "design:approve", "roles": ["lead"], "quorum": "all"}]
}
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"summary": {"type": "string"}}}


def _workspace(tmp_path: Path, funnel: dict[str, Any] | None, *, gated: bool = True) -> Path:
    root = tmp_path / "ws"
    for sub in ("topologies", "funnels", "schemas"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "w", "name": "w"},
            }
        )
    )
    (root / "schemas" / "spec.schema.json").write_text(json.dumps(SCHEMA))
    if funnel is not None:
        (root / "funnels" / "spec-review.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "swarmkit/v1",
                    "kind": "Funnel",
                    "metadata": {
                        "id": "spec-review",
                        "name": "Spec Review",
                        "description": "the funnel whose strength this test measures",
                    },
                    "approve": APPROVE,
                    "provenance": {"authored_by": "human", "version": "1.0.0"},
                    **funnel,
                }
            )
        )
    agent: dict[str, Any] = {
        "id": "designer",
        "role": "root",
        "model": {"provider": "mock", "name": "mock"},
    }
    if gated and funnel is not None:
        agent["funnel"] = "spec-review"
    (root / "topologies" / "design.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Topology",
                "metadata": {"name": "design", "version": "0.1.0"},
                "agents": {"root": agent},
            }
        )
    )
    return root


def _report(root: Path) -> Any:
    return WorkspaceRuntime.from_workspace_path(root).verification()


# ---- the finding -----------------------------------------------------------------------------


def test_a_root_with_no_funnel_is_unverified(tmp_path: Path) -> None:
    """The headline: this run's answer is whatever the model said."""
    report = _report(_workspace(tmp_path, None))

    assert report.ok is False
    assert [a.agent_id for a in report.unverified_roots] == ["designer"]
    assert "checked by nothing" in report.unverified_roots[0].line()


def test_a_gated_root_is_verified(tmp_path: Path) -> None:
    report = _report(_workspace(tmp_path, {"judge": {"skill": "spec-judge"}}))

    assert report.ok is True
    assert report.unverified_roots == ()


def test_strength_rises_with_each_wired_layer(tmp_path: Path) -> None:
    approve_only = _report(_workspace(tmp_path / "a", {}))
    judged = _report(_workspace(tmp_path / "b", {"judge": {"skill": "spec-judge"}}))

    assert approve_only.weakest.strength < judged.weakest.strength


# ---- and it counts what RUNS, not what is written ------------------------------------------------


def test_a_declared_but_unwired_layer_does_not_count(tmp_path: Path) -> None:
    """The property that stops this check repeating the defect it sits beside.

    `review:` is declared in the Funnel schema and built by nothing, so a strength score that
    counted declarations would over-state how checked this output is — which is exactly the
    "declared, accepted, displayed, loaded by nothing" shape the reachability report exists for.
    """
    report = _report(
        _workspace(tmp_path, {"judge": {"skill": "spec-judge"}, "review": {"archetype": "r"}})
    )
    root = report.weakest

    assert "review" in root.declared
    assert "review" not in root.layers
    assert "review" in root.inert


def test_an_unresolvable_validate_schema_is_inert(tmp_path: Path) -> None:
    """The same shape found in the shipped example workspace: a `validate.schema` naming a file
    that does not exist builds no validator, so the layer is declared and does nothing."""
    report = _report(_workspace(tmp_path, {"validate": {"schema": "schemas/not-written.json"}}))

    assert "validate" in report.weakest.inert


def test_a_resolvable_validate_schema_counts(tmp_path: Path) -> None:
    report = _report(_workspace(tmp_path, {"validate": {"schema": "schemas/spec.schema.json"}}))

    assert "validate" in report.weakest.layers
    assert report.weakest.inert == ()


# ---- only roots are findings ---------------------------------------------------------------------


def test_every_agent_is_reported_but_only_roots_are_findings(tmp_path: Path) -> None:
    """A leaf worker returning a fact to its parent is not producing a reviewable artifact, and
    flagging every agent would make a report nobody reads."""
    root = _workspace(tmp_path, None)
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
                        "model": {"provider": "mock", "name": "mock"},
                        "children": [
                            {
                                "id": "helper",
                                "role": "worker",
                                "model": {"provider": "mock", "name": "mock"},
                            }
                        ],
                    }
                },
            }
        )
    )
    report = _report(root)

    assert {a.agent_id for a in report.agents} == {"designer", "helper"}
    assert [a.agent_id for a in report.unverified_roots] == ["designer"]


# ---- the pure function, without a compile --------------------------------------------------------


def test_the_analysis_is_pure(tmp_path: Path) -> None:
    """One question, one implementation: `validate`, the endpoint and a test all call this."""
    root = _workspace(tmp_path, {"judge": {"skill": "spec-judge"}})
    workspace = resolve_workspace(root)

    ledger = WiringLedger()
    ledger.wired("funnel_layer", "designer:spec-review:judge")

    report = compute_verification(workspace, ledger)

    assert report.weakest is not None
    assert report.weakest.layers == ("judge",)
    assert report.ok is True


def test_an_empty_ledger_means_nothing_is_wired(tmp_path: Path) -> None:
    """A funnel declared and a compile that built none of it: strength zero, and the root is a
    finding despite carrying a funnel."""
    root = _workspace(tmp_path, {"judge": {"skill": "spec-judge"}})

    report = compute_verification(resolve_workspace(root), WiringLedger())

    assert report.weakest is not None
    assert report.weakest.strength == 0
    assert report.ok is False


# ---- the surfaces --------------------------------------------------------------------------------


def test_the_report_serialises(tmp_path: Path) -> None:
    payload = _report(_workspace(tmp_path, None)).to_dict()

    assert payload["ok"] is False
    assert payload["unverified_roots"]
    assert json.dumps(payload), "must be JSON-serialisable"


@pytest.mark.parametrize(
    "needle",
    ['@app.get("/workspace/verification")', "verification().to_dict()"],
)
def test_the_endpoint_is_registered(needle: str) -> None:
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/server/_routes_introspection.py"
    ).read_text()

    assert needle in src


def test_validate_gates_on_it_under_its_own_flag() -> None:
    """`--require` is reachability and `--require-verified` is this: "is my config wired" and "is my
    output checked" are different questions a CI job may want independently."""
    src = (
        Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/cli/_cmd_authoring.py"
    ).read_text()

    assert "--require-verified" in src
    assert "require_verified and verification is not None and not verification.ok" in src
