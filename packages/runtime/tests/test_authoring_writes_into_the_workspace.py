"""An authoring session writes where it was pointed, and can confirm a write at all.

`swarmkit init fresh/` produced files in the current directory on one turn and in a
`support-swarm/` the model invented on the next — `write_files` took its `base_dir`
from the model. And under prompt_toolkit the "[Y/n]" confirmation is asked from inside
the agent's tool call, on the running event loop, where `prompt()` raised
"asyncio.run() cannot be called from a running event loop": every session that reached
the write step crashed there.

After #1045 PR 4 the tool is renamed ``write_file`` (singular, matching the bundled
skill id) and routes through the IAM-scoped command_pack script. The script resolves
the target workspace from ``SWARMKIT_AUTHOR_TARGET_WORKSPACE``, not from any
``base_dir`` the model names — so the model-chosen-base-dir regression is now
structurally impossible, not just handled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.authoring import _agent
from swarmkit_runtime.model_providers import ContentBlock


def test_write_file_lands_in_session_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    _agent._session_state["workspace"] = workspace
    _agent._session_state["write_attempt"] = 0
    monkeypatch.setattr(_agent, "_read_confirm", lambda: True)
    monkeypatch.setattr(_agent, "_print_agent", lambda *_a, **_k: None)
    monkeypatch.setattr(_agent, "_print_status", lambda *_a, **_k: None)
    call: Any = ContentBlock(
        type="tool_use",
        tool_name="write_file",
        tool_use_id="t",
        tool_input={
            "files": {
                "workspace.yaml": (
                    "apiVersion: swarmkit/v1\nkind: Workspace\n"
                    "metadata: {id: fresh, name: Fresh, description: a test workspace}\n"
                    "governance: {provider: mock}\n"
                )
            },
        },
    )
    result = _agent._handle_tool_call(call)
    assert (workspace / "workspace.yaml").exists()
    assert '"written"' in result and "workspace.yaml" in result


def test_write_file_rejects_scope_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model cannot escape the session workspace — the script refuses before writing."""
    workspace = tmp_path / "scoped"
    workspace.mkdir()
    _agent._session_state["workspace"] = workspace
    _agent._session_state["write_attempt"] = 0
    monkeypatch.setattr(_agent, "_read_confirm", lambda: True)
    monkeypatch.setattr(_agent, "_print_agent", lambda *_a, **_k: None)
    monkeypatch.setattr(_agent, "_print_status", lambda *_a, **_k: None)
    call: Any = ContentBlock(
        type="tool_use",
        tool_name="write_file",
        tool_use_id="t",
        tool_input={"files": {"../escape.yaml": "evil: true\n"}},
    )
    result = _agent._handle_tool_call(call)
    assert '"error"' in result
    assert "write_scope_violation" in result
    assert not (tmp_path / "escape.yaml").exists()
