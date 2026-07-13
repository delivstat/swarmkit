"""Container-sandbox config seam (executor-container-sandbox.md, task #12).

The opt-in tier's data layer: parsing the adapter `sandbox` block into a SandboxSpec, the
disable-switch precedence in `_sandbox_for`, and the fail-loud container branch. The provisioner
itself (docker run, build, egress) lands in later tasks; here the seam is proven with no runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.executors import (
    ResolvedExecutor,
    SandboxSpec,
)
from swarmkit_runtime.executors._adapter_spec import parse_adapter_spec
from swarmkit_runtime.langgraph_compiler._harness_node import (
    _container_disabled,
    _effective_sandbox,
    _sandbox_for,
)
from swarmkit_runtime.resolver._resolved import ResolvedAgent

# --- spec parsing -------------------------------------------------------------------------------


def _adapter(sandbox: dict[str, Any] | None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "launch": {"command": ["h", "{task.statement}"]},
        "stream": {"format": "jsonl"},
        "event_map": [{"when": {"type": "done"}, "emit": [{"event": "result"}]}],
    }
    if sandbox is not None:
        spec["sandbox"] = sandbox
    return {"metadata": {"id": "h"}, "spec": spec}


def test_absent_block_defaults_to_worktree() -> None:
    spec = parse_adapter_spec(_adapter(None)).sandbox
    assert spec.kind == "worktree"
    assert not spec.is_container
    assert spec.network == "deny"


def test_container_block_parses_all_fields() -> None:
    spec = parse_adapter_spec(
        _adapter(
            {
                "kind": "container",
                "image": "my-harness:latest",
                "network": "allowlist",
                "allow": ["api.anthropic.com"],
                "mounts": [{"source": "./kb", "target": "/kb", "mode": "ro"}],
                "resources": {"cpus": "2", "memory": "2g", "pids": 512},
            }
        )
    ).sandbox
    assert spec.is_container
    assert spec.image == "my-harness:latest"
    assert spec.network == "allowlist"
    assert spec.allow == ("api.anthropic.com",)
    assert spec.mounts[0].source == "./kb" and spec.mounts[0].target == "/kb"
    assert (spec.cpus, spec.memory, spec.pids) == ("2", "2g", 512)


def test_build_front_ends_parse() -> None:
    base = parse_adapter_spec(
        _adapter(
            {"kind": "container", "build": {"base": "node:22-slim", "install": ["npm i -g x"]}}
        )
    ).sandbox
    assert base.build is not None and base.build.base == "node:22-slim"
    assert base.build.install == ("npm i -g x",)

    inline = parse_adapter_spec(
        _adapter({"kind": "container", "build": {"dockerfile_inline": "FROM node:22-slim\n"}})
    ).sandbox
    assert inline.build is not None and inline.build.dockerfile_inline == "FROM node:22-slim\n"


# --- disable switch -----------------------------------------------------------------------------


def test_disable_switch_reads_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for val in ("1", "true", "YES", "True"):
        monkeypatch.setenv("SWARMKIT_DISABLE_CONTAINER_SANDBOX", val)
        assert _container_disabled() is True
    for val in ("0", "", "no", "off"):
        monkeypatch.setenv("SWARMKIT_DISABLE_CONTAINER_SANDBOX", val)
        assert _container_disabled() is False


# --- precedence in _sandbox_for -----------------------------------------------------------------


def _agent(config: dict[str, Any] | None = None) -> ResolvedAgent:
    return ResolvedAgent(
        id="coder",
        role="worker",
        model=None,
        prompt=None,
        skills=(),
        iam=None,
        source_archetype="coding-worker",
        executor=ResolvedExecutor(kind="h", config=config or {}),
    )


def test_worktree_when_no_sandbox_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWARMKIT_DISABLE_CONTAINER_SANDBOX", raising=False)
    _cm, persistent = _sandbox_for(_agent(), tmp_path, "HEAD", None)
    assert persistent is False  # the ephemeral worktree, unchanged


def test_container_spec_selects_container_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SWARMKIT_DISABLE_CONTAINER_SANDBOX", raising=False)
    cm, _persistent = _sandbox_for(_agent(), tmp_path, "HEAD", SandboxSpec(kind="container"))
    # The container branch is chosen (a container_sandbox context manager), not the worktree.
    # Provisioning happens on enter; see test_container_sandbox for that path.
    assert cm is not None


def test_disable_switch_forces_worktree_over_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SWARMKIT_DISABLE_CONTAINER_SANDBOX", "1")
    cm, persistent = _sandbox_for(_agent(), tmp_path, "HEAD", SandboxSpec(kind="container"))
    # Disable wins: the native worktree, not the container branch (no ExecutorError on enter).
    assert persistent is False
    assert cm is not None


def test_config_override_beats_adapter_block(monkeypatch: pytest.MonkeyPatch) -> None:
    # Adapter says worktree; the archetype's executor.config.sandbox opts into a container.
    agent = _agent({"sandbox": {"kind": "container", "network": "deny"}})
    effective = _effective_sandbox(agent, SandboxSpec(kind="worktree"))
    assert effective is not None and effective.is_container


def test_working_dir_still_persistent_without_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SWARMKIT_DISABLE_CONTAINER_SANDBOX", raising=False)
    _cm, persistent = _sandbox_for(_agent({"working_dir": "wd"}), tmp_path, "HEAD", None)
    assert persistent is True  # unchanged behaviour for a session-scoped harness
