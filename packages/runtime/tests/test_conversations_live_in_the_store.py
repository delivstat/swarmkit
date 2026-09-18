"""Conversations persist in the workspace's runtime store, not in `.swarmkit/conversations/*.json`.

The store had a `conversations` table since the persistence service landed (`StoreKind.RUNTIME`:
"jobs, conversations, usage") and nothing wrote to it: `ConversationManager` kept writing JSON
files, so a workspace configured for Postgres still had its chats on local disk — the exact
split the storage service exists to prevent. Conversations saved as files by earlier versions are
adopted into the store on resume.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from swarmkit_runtime._conversation import ConversationManager
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime

_WS = """apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: chat, name: Chat}
governance: {provider: mock}
"""
_TOPO = """apiVersion: swarmkit/v1
kind: Topology
metadata: {name: hello, version: 0.1.0}
agents:
  root:
    id: greeter
    role: root
    model: {provider: mock, name: m}
    prompt: {system: greet}
"""


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_QUIET", "1")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "topologies" / "hello.yaml").write_text(_TOPO)
    return tmp_path


@pytest.mark.asyncio
async def test_a_turn_is_saved_to_the_store_and_resumes_from_it(ws: Path) -> None:
    rt = WorkspaceRuntime.from_workspace_path(ws)
    manager = ConversationManager(rt, ws)
    conv = manager.create("hello")
    await manager.send(conv, "hi")

    # No file. The row is in the store the storage service resolved.
    assert not (ws / ".swarmkit" / "conversations").exists()
    row = rt.store.get_conversation(conv.id)
    assert row is not None and [t["role"] for t in row.turns] == ["human", "swarm"]

    resumed = ConversationManager(rt, ws).resume(conv.id[:4])
    assert resumed is not None and resumed.turns[0].content == "hi"
    listed = manager.list_conversations()
    assert listed[0]["id"] == conv.id and listed[0]["turns"] == "2"


def test_a_legacy_file_conversation_is_adopted_on_resume(ws: Path) -> None:
    legacy = ws / ".swarmkit" / "conversations"
    legacy.mkdir(parents=True)
    (legacy / "abcd1234.json").write_text(
        json.dumps(
            {
                "id": "abcd1234",
                "workspace_path": str(ws),
                "topology_name": "hello",
                "turns": [{"role": "human", "content": "old", "timestamp": "", "events": []}],
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        )
    )
    rt = WorkspaceRuntime.from_workspace_path(ws)
    conv = ConversationManager(rt, ws).resume("abcd")
    assert conv is not None and conv.turns[0].content == "old"
    assert rt.store.get_conversation("abcd1234") is not None
