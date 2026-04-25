"""Tests for CLI governance provider selection (M2 wiring).

Verifies that ``build_governance`` (from ``_workspace_runtime``) reads
the workspace's ``governance:`` block and instantiates the correct
provider. Does not hit real AGT — test_agt_governance.py covers that.
"""

from __future__ import annotations

from pathlib import Path

from swarmkit_runtime._workspace_runtime import build_governance
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.resolver import resolve_workspace

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "workspaces"


def test_absent_governance_block_returns_mock() -> None:
    """hello-swarm workspace has no governance block → MockGovernanceProvider."""
    workspace = resolve_workspace(EXAMPLE_WS)
    gov = build_governance(workspace, EXAMPLE_WS)
    assert isinstance(gov, MockGovernanceProvider)


def test_agt_governance_returns_agt_provider(tmp_path: Path) -> None:
    """A workspace declaring ``governance.provider: agt`` gets AGTGovernanceProvider."""
    from swarmkit_runtime.governance.agt_provider import AGTGovernanceProvider  # noqa: PLC0415

    # Create a minimal workspace with governance: agt
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: Workspace\n"
        "metadata:\n"
        "  id: test-agt\n"
        "  name: Test AGT\n"
        "governance:\n"
        "  provider: agt\n"
        "  config:\n"
        "    policies_dir: ./policies\n"
    )
    policies_dir = ws_root / "policies"
    policies_dir.mkdir()

    workspace = resolve_workspace(ws_root)
    gov = build_governance(workspace, ws_root)
    assert isinstance(gov, AGTGovernanceProvider)


def test_mock_governance_explicit_returns_mock(tmp_path: Path) -> None:
    """A workspace declaring ``governance.provider: mock`` → MockGovernanceProvider."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: Workspace\n"
        "metadata:\n"
        "  id: test-mock\n"
        "  name: Test Mock\n"
        "governance:\n"
        "  provider: mock\n"
    )

    workspace = resolve_workspace(ws_root)
    gov = build_governance(workspace, ws_root)
    assert isinstance(gov, MockGovernanceProvider)


def test_custom_governance_falls_back_to_mock(tmp_path: Path) -> None:
    """``governance.provider: custom`` is not yet implemented → Mock with warning."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\n"
        "kind: Workspace\n"
        "metadata:\n"
        "  id: test-custom\n"
        "  name: Test Custom\n"
        "governance:\n"
        "  provider: custom\n"
    )

    workspace = resolve_workspace(ws_root)
    gov = build_governance(workspace, ws_root)
    assert isinstance(gov, MockGovernanceProvider)
