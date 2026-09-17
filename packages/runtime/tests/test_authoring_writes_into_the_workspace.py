"""An authoring session writes where it was pointed, and can confirm a write at all.

`swarmkit init fresh/` produced files in the current directory on one turn and in a
`support-swarm/` the model invented on the next — `write_files` took its `base_dir` from the
model. And under prompt_toolkit the "[Y/n]" confirmation is asked from inside the agent's tool
call, on the running event loop, where `prompt()` raised "asyncio.run() cannot be called from a
running event loop": every session that reached the write step crashed there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.authoring import _agent
from swarmkit_runtime.model_providers import ContentBlock


def test_write_files_goes_to_the_session_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "fresh"
    workspace.mkdir()
    _agent._session_state["workspace"] = workspace
    _agent._session_state["write_attempt"] = 0
    monkeypatch.setattr(_agent, "_read_confirm", lambda: True)
    monkeypatch.setattr(_agent, "_print_agent", lambda *_a, **_k: None)
    monkeypatch.setattr(_agent, "_print_status", lambda *_a, **_k: None)
    elsewhere = tmp_path / "support-swarm"  # the base_dir the model chose
    call: Any = ContentBlock(
        type="tool_use",
        tool_name="write_files",
        tool_use_id="t",
        tool_input={
            "base_dir": str(elsewhere),
            "files": {
                "workspace.yaml": (
                    "apiVersion: swarmkit/v1\nkind: Workspace\n"
                    "metadata: {id: fresh, name: Fresh}\ngovernance: {provider: mock}\n"
                )
            },
        },
    )
    _agent._handle_tool_call(call)
    assert (workspace / "workspace.yaml").exists()
    assert not elsewhere.exists()
