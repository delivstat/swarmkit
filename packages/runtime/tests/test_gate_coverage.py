"""Gate coverage — slice 1 of gate-coverage-and-comprehension-debt.

Unit tests build a minimal ResolvedWorkspace from schema models to exercise the
classifier's edge behavior; the integration test runs the real function + the
`swarmkit gates` CLI over the SDLC example pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.cli import app
from swarmkit_runtime.gate_coverage import (
    UnknownPipelineError,
    compute_gate_coverage,
)
from swarmkit_runtime.resolver import resolve_workspace
from swarmkit_runtime.resolver._resolved import (
    ResolvedFunnel,
    ResolvedStageGraph,
    ResolvedWorkspace,
)
from swarmkit_runtime.server import create_app
from swarmkit_schema.models.funnel import SwarmKitFunnel
from swarmkit_schema.models.stage_graph import SwarmKitStageGraph
from swarmkit_schema.models.workspace import SwarmKitWorkspace
from typer.testing import CliRunner

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SDLC_WS = _REPO_ROOT / "examples" / "sdlc-pipeline" / "workspace"

_FUNNEL_FULL = {
    "apiVersion": "swarmkit/v1",
    "kind": "Funnel",
    "metadata": {"id": "full-gate", "name": "Full gate", "description": "coverage test fixture"},
    "validate": {"schema": "schemas/x.json"},
    "judge": {"skill": "artifact-judge", "threshold": 0.8},
    "review": {"archetype": "reviewer"},
    "approve": {"rules": [{"scope": "x:approve", "roles": ["lead"], "quorum": "all"}]},
    "provenance": {"authored_by": "human", "version": "1.0.0"},
}
_FUNNEL_APPROVE_ONLY = {
    "apiVersion": "swarmkit/v1",
    "kind": "Funnel",
    "metadata": {
        "id": "approve-only",
        "name": "Approve only",
        "description": "coverage test fixture",
    },
    "approve": {"rules": [{"scope": "x:approve", "roles": ["lead"], "quorum": "all"}]},
    "provenance": {"authored_by": "human", "version": "1.0.0"},
}


def _make_ws(stages: list[dict[str, Any]], funnels: dict[str, dict[str, Any]]) -> ResolvedWorkspace:
    """A minimal ResolvedWorkspace carrying one pipeline + the given funnels."""
    sg_raw = SwarmKitStageGraph.model_validate(
        {
            "apiVersion": "swarmkit/v1",
            "kind": "StageGraph",
            "metadata": {"id": "p", "name": "P", "description": "coverage test fixture"},
            "stages": stages,
            "provenance": {"authored_by": "human", "version": "1.0.0"},
        }
    )
    resolved_funnels = {
        fid: ResolvedFunnel(
            id=fid,
            raw=SwarmKitFunnel.model_validate(spec),
            source_path=Path(f"{fid}.yaml"),
            spec=spec,
        )
        for fid, spec in funnels.items()
    }
    ws_raw = SwarmKitWorkspace.model_validate(
        {
            "apiVersion": "swarmkit/v1",
            "kind": "Workspace",
            "metadata": {"id": "w", "name": "W", "description": "coverage test fixture"},
        }
    )
    return ResolvedWorkspace(
        raw=ws_raw,
        source_path=Path("."),
        topologies={},
        skills={},
        archetypes={},
        triggers=(),
        funnels=resolved_funnels,
        stage_graphs={
            "p": ResolvedStageGraph(id="p", raw=sg_raw, source_path=Path("p.yaml"), spec={})
        },
    )


def test_passthrough_and_external_entry() -> None:
    ws = _make_ws(
        stages=[
            {"id": "a", "topology": "t", "when": ["ext.event"], "success": "a.done"},
            {"id": "b", "topology": "t", "when": ["a.done"]},
        ],
        funnels={},
    )
    cov = compute_gate_coverage(ws, "p")
    a, b = cov.stages
    assert a.gate_class == "passthrough"
    assert a.external_entry is True  # ext.event is emitted by no stage
    assert a.terminal is False  # a.done consumed by b
    assert b.external_entry is False  # a.done is emitted internally
    assert b.terminal is True  # nothing consumes b's (absent) success


def test_human_gate_pre_filters() -> None:
    ws = _make_ws(
        stages=[
            {"id": "a", "topology": "t", "when": ["start"], "gate": "full-gate", "success": "a.ok"},
            {"id": "b", "topology": "t", "when": ["a.ok"], "gate": "approve-only"},
        ],
        funnels={"full-gate": _FUNNEL_FULL, "approve-only": _FUNNEL_APPROVE_ONLY},
    )
    cov = compute_gate_coverage(ws, "p")
    a, b = cov.stages
    assert a.gate_class == "human"
    assert a.pre_filters == ("validate", "judge", "review")
    assert a.strength == 4
    assert b.gate_class == "human"
    assert b.pre_filters == ()  # approve-only funnel — still human, no pre-filters
    assert b.strength == 1


def test_narrowest_prefers_passthrough_then_thin_prefilters() -> None:
    ws = _make_ws(
        stages=[
            {
                "id": "gated",
                "topology": "t",
                "when": ["start"],
                "gate": "full-gate",
                "success": "gated.ok",
            },
            {"id": "raw", "topology": "t", "when": ["gated.ok"], "success": "raw.ok"},
            {"id": "last", "topology": "t", "when": ["raw.ok"]},
        ],
        funnels={"full-gate": _FUNNEL_FULL},
    )
    cov = compute_gate_coverage(ws, "p")
    assert cov.narrowest is not None
    assert cov.narrowest.stage_id == "raw"  # passthrough beats the human-gated stage
    assert {s.stage_id for s in cov.passthrough} == {"raw"}  # 'last' is terminal, excluded
    assert "passthrough" in cov.verdict()
    assert cov.violates("human") == cov.passthrough


def test_all_human_verdict() -> None:
    ws = _make_ws(
        stages=[
            {"id": "a", "topology": "t", "when": ["start"], "gate": "full-gate", "success": "a.ok"},
            {"id": "b", "topology": "t", "when": ["a.ok"]},
        ],
        funnels={"full-gate": _FUNNEL_FULL},
    )
    cov = compute_gate_coverage(ws, "p")
    assert cov.passthrough == ()
    assert cov.violates("human") == ()
    assert "human-gated" in cov.verdict()


def test_unknown_pipeline_raises() -> None:
    ws = _make_ws(stages=[{"id": "a", "topology": "t"}], funnels={})
    with pytest.raises(UnknownPipelineError):
        compute_gate_coverage(ws, "nope")


# ---- integration over the real SDLC example ----------------------------------


@pytest.fixture(scope="module")
def sdlc_ws() -> ResolvedWorkspace:
    return resolve_workspace(_SDLC_WS)


def test_sdlc_full_coverage(sdlc_ws: ResolvedWorkspace) -> None:
    cov = compute_gate_coverage(sdlc_ws, "sdlc-full")
    by_id = {s.stage_id: s for s in cov.stages}
    # design / security-review / deploy carry human funnels; the build/test stages don't.
    assert by_id["design"].gate_class == "human"
    assert by_id["security-review"].gate_class == "human"
    assert by_id["deploy"].gate_class == "human"
    assert by_id["build"].gate_class == "passthrough"
    assert by_id["support-handover"].terminal is True
    # the pipeline is triggered externally (requirement.created)
    assert by_id["intake"].external_entry is True
    # there ARE unverified edges, and the narrowest is one of them
    assert cov.narrowest is not None
    assert cov.narrowest.gate_class == "passthrough"


def test_gates_cli_and_require_floor(sdlc_ws: ResolvedWorkspace) -> None:
    runner = CliRunner()
    ok = runner.invoke(app, ["gates", str(_SDLC_WS), "--pipeline", "sdlc-full"])
    assert ok.exit_code == 0, ok.output
    assert "narrowest verified edge" in ok.output
    assert "passthrough" in ok.output

    # the SDLC pipeline has passthrough edges → --require human fails (exit 1)
    strict = runner.invoke(
        app, ["gates", str(_SDLC_WS), "--pipeline", "sdlc-full", "--require", "human"]
    )
    assert strict.exit_code == 1, strict.output


def test_gate_coverage_endpoint() -> None:
    with TestClient(create_app(_SDLC_WS)) as c:
        r = c.get("/api/pipelines/sdlc-full/gate-coverage")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["pipeline"] == "sdlc-full"
        assert data["narrowest"] is not None
        assert any(s["gate"] == "human" for s in data["stages"])
        assert any(s["gate"] == "passthrough" for s in data["stages"])
        assert c.get("/api/pipelines/nope/gate-coverage").status_code == 404
