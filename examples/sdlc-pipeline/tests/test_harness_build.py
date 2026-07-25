"""Slice 7 — the harness build (candidate diff) + the code-review gate. The executor showcase.

Covers design/details/sdlc-pipeline-example.md (build-order item 7) and executor-abstraction.md:

  - the ``oms-build-harness`` topology resolves + compiles, and the ``developer`` node's HARNESS
    executor block is accepted (``kind: harness`` selecting the ``claude-code`` adapter by ``ref``);
  - the ``oms-code-review`` funnel resolves and its approver role confers the scope it signs off;
  - the harness produces a candidate diff (files + manifest + status + captured session + cost),
    driven through the *real* bundled ``claude-code`` adapter with only the subprocess faked;
  - the code-review gate advances on a clean review (retries=0) and routes the critique back to the
    harness on a finding, which revises and then passes (retries=1).

The example ships under ``examples/sdlc-pipeline`` (not an installed runtime feature), so the
directory is put on ``sys.path`` to import the demo modules — same as the other slice tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from swarmkit_runtime.executors._declarative import load_adapter_specs
from swarmkit_runtime.executors._events import ExecArtifact, ExecResult, ExecToolCall
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.langgraph_compiler import compile_topology
from swarmkit_runtime.model_providers._mock import MockModelProvider
from swarmkit_runtime.resolver import resolve_workspace

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLE = _REPO_ROOT / "examples" / "sdlc-pipeline"
_WS = _EXAMPLE / "workspace"
if str(_EXAMPLE) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE))

import demo_harness_build as demo  # type: ignore[import-not-found]  # noqa: E402
import harness_build as hb  # type: ignore[import-not-found]  # noqa: E402

# --------------------------------------------------------------------------------------------
# Topology: resolves + compiles, and the developer's HARNESS executor block is accepted
# --------------------------------------------------------------------------------------------


def test_build_topology_resolves_with_harness_executor() -> None:
    ws = resolve_workspace(_WS)
    topo = ws.topologies["oms-build-harness"]
    builder = topo.root

    assert builder.id == "builder"
    assert builder.source_archetype == "developer"
    # the executor showcase: kind=harness, adapter selected by ref (executor-abstraction §4.2).
    assert builder.executor is not None
    assert builder.executor.kind == "harness"
    assert builder.executor.ref == "claude-code"
    # the code-review gate is attached to the developer node.
    assert builder.funnel is not None and builder.funnel.id == "oms-code-review"
    # scoped to the OMS app repo — it does not hold another app's scope.
    scopes = (builder.iam or {}).get("base_scope", [])
    assert "app:oms:read" in scopes and "repo:write" in scopes
    assert not any(s.startswith("app:web") or s.startswith("app:mobile") for s in scopes)

    graph = compile_topology(
        topo, model_provider=MockModelProvider(), governance=MockGovernanceProvider(allow_all=True)
    )
    assert graph is not None


def test_bundled_claude_code_adapter_is_available() -> None:
    """The build reuses the runtime's bundled adapter library — it is not re-created here."""
    specs = load_adapter_specs()
    assert "claude-code" in specs
    assert "opencode" in specs  # the swap target — one `executor.ref` change, per the design


def test_code_review_funnel_role_resolves_and_confers_scope() -> None:
    ws = resolve_workspace(_WS)
    spec = ws.funnels["oms-code-review"].spec
    registry = ws.role_registry
    # judge is the code-review decision skill; approve is the OMS lead.
    assert spec["judge"]["skill"] == "code-review"
    for rule in spec["approve"]["rules"]:
        for role in rule["roles"]:
            assert registry.get(role) is not None
            assert registry.confers(role, rule["scope"])


# --------------------------------------------------------------------------------------------
# Harness build: a candidate diff is produced through the real adapter (subprocess faked)
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_harness_produces_candidate_diff() -> None:
    diff = await hb.run_developer_harness()

    # a real terminal exec.result flowed through the adapter, and it carries a diff.
    assert any(isinstance(e, ExecResult) for e in diff.events)
    assert diff.status == "success"
    assert diff.diff.startswith("diff --git")
    # the manifest matches the declared `files` profile: one file_change per touched file.
    assert diff.files_changed == ("orders.py", "README.md")
    assert all(
        isinstance(a, ExecArtifact) and a.artifact_kind == "file_change" for a in diff.manifest
    )
    assert len(diff.manifest) == len(diff.files_changed)
    # the tool trail + captured vendor session (resume seam) + cost came off the event stream.
    assert [e.tool for e in diff.events if isinstance(e, ExecToolCall)] == ["Read", "Edit", "Bash"]
    assert diff.session_id == "sess-oms-build"
    assert diff.cost_usd == 0.42


@pytest.mark.asyncio
async def test_flawed_vs_revised_diff_differ_on_the_guard() -> None:
    flawed = await hb.run_developer_harness(revised=False)
    revised = await hb.run_developer_harness(revised=True)
    assert "LookupError" not in flawed.diff  # the planted finding: unguarded lookup
    assert "LookupError" in revised.diff  # the revision adds the guard
    assert revised.added > flawed.added


# --------------------------------------------------------------------------------------------
# The code-review gate: advance on clean, route-back to the harness on a finding
# --------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_advances_on_clean_review() -> None:
    ws = resolve_workspace(_WS)
    run = await demo.run_code_review_gate(
        ws, verdicts=[True], clean_first=True, correlation_id="TEST-CR-1"
    )
    assert run.outcome == "approved"
    assert run.retries == 0  # a clean review advances with no route-back
    assert run.approver == "alice"  # the OMS lead signed off
    assert len(run.diffs) == 1
    assert "LookupError" in run.diffs[0].diff  # got it right first time


@pytest.mark.asyncio
async def test_gate_routes_back_to_harness_on_finding_then_passes() -> None:
    ws = resolve_workspace(_WS)
    run = await demo.run_code_review_gate(
        ws, verdicts=[False, True], clean_first=False, correlation_id="TEST-CR-2"
    )
    # the finding routed back once; the harness revised and the revision cleared review + approval.
    assert run.retries == 1
    assert run.outcome == "approved"
    assert run.approver == "alice"
    # two harness runs: the flawed first pass, then the guarded revision.
    assert len(run.diffs) == 2
    assert "LookupError" not in run.diffs[0].diff
    assert "LookupError" in run.diffs[1].diff
