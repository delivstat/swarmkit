"""Launch-block review gate for workspace adapters (executor-abstraction §5.2, P3 PR6)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from swarmkit_runtime.executors import (
    ExecutorError,
    ResolvedExecutor,
    approve_launch,
    is_launch_approved,
    launch_fingerprint,
    load_workspace_adapter_specs,
    parse_adapter_spec,
)
from swarmkit_runtime.langgraph_compiler._harness_node import _build_executor

_ADAPTER = """apiVersion: swarmkit/v1
kind: ExecutorAdapter
metadata: {{id: my-harness, name: My Harness, description: a workspace adapter for tests}}
spec:
  launch: {{command: [my-harness, run, "{extra}"]}}
  stream: {{format: jsonl}}
  event_map: [{{emit: [{{event: result, with: {{status: success}}}}]}}]
provenance: {{authored_by: human, version: 0.1.0}}
"""


def _workspace(root: Path, extra: str = "x") -> None:
    (root / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: w, name: W}\n"
    )
    adapters = root / "adapters"
    adapters.mkdir(exist_ok=True)
    (adapters / "my-harness.yaml").write_text(_ADAPTER.format(extra=extra))


def _spec(root: Path) -> object:
    return load_workspace_adapter_specs(root)["my-harness"]


def test_fingerprint_is_stable_and_changes_with_the_launch(tmp_path: Path) -> None:
    a = parse_adapter_spec(yaml.safe_load(_ADAPTER.format(extra="one")))
    a2 = parse_adapter_spec(yaml.safe_load(_ADAPTER.format(extra="one")))
    b = parse_adapter_spec(yaml.safe_load(_ADAPTER.format(extra="two")))
    assert launch_fingerprint(a) == launch_fingerprint(a2)  # stable
    assert launch_fingerprint(a) != launch_fingerprint(b)  # changes with the launch surface


def test_unapproved_then_approved(tmp_path: Path) -> None:
    _workspace(tmp_path)
    spec = _spec(tmp_path)
    assert not is_launch_approved(tmp_path, spec)  # type: ignore[arg-type]
    approve_launch(tmp_path, spec)  # type: ignore[arg-type]
    assert is_launch_approved(tmp_path, spec)  # type: ignore[arg-type]
    # the approval is a durable on-disk record
    assert (tmp_path / ".swarmkit/adapters-approved.json").is_file()


def test_changing_the_launch_invalidates_approval(tmp_path: Path) -> None:
    _workspace(tmp_path, extra="original")
    approve_launch(tmp_path, _spec(tmp_path))  # type: ignore[arg-type]
    assert is_launch_approved(tmp_path, _spec(tmp_path))  # type: ignore[arg-type]
    # edit the launch command → prior approval no longer matches
    _workspace(tmp_path, extra="TAMPERED")
    assert not is_launch_approved(tmp_path, _spec(tmp_path))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_harness_node_refuses_unapproved_workspace_adapter(tmp_path: Path) -> None:
    _workspace(tmp_path)
    with pytest.raises(ExecutorError, match="not approved"):
        _build_executor(ResolvedExecutor(kind="my-harness"), tmp_path)

    # after approval it builds a runnable executor
    approve_launch(tmp_path, _spec(tmp_path))  # type: ignore[arg-type]
    ex = _build_executor(ResolvedExecutor(kind="my-harness"), tmp_path)
    assert ex.kind == "my-harness"


def test_bundled_adapter_bypasses_the_gate(tmp_path: Path) -> None:
    # claude-code is bundled + pre-vetted → no approval needed even with no approval file.
    (tmp_path / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: w, name: W}\n"
    )
    ex = _build_executor(ResolvedExecutor(kind="claude-code"), tmp_path)
    assert ex.kind == "claude-code"
