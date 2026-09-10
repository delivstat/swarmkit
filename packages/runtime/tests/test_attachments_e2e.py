"""End to end: a caller attaches an image and the model actually receives it.

``design/details/images-on-both-executors.md``. Everything else about this feature can pass while
the one thing that matters does not happen — the bytes reaching the provider's request. So this
runs a real topology through ``WorkspaceRuntime`` and inspects what the model provider was handed,
rather than trusting the layers in between.

It also pins the two properties that are easy to lose later: an unattached run must produce a
byte-identical request to before the feature existed, and the audit record must describe the
attachment without containing it.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.model_providers import MockModelProvider

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

WORKSPACE = """\
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: attach-test
  name: Attachment test
"""

TOPOLOGY = """\
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: look
  version: 0.1.0
agents:
  root:
    id: root
    role: root
    model:
      provider: mock
      name: mock
    prompt:
      system: You look at what you are given.
"""


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_MODEL", "mock")
    (tmp_path / "topologies").mkdir()
    (tmp_path / "workspace.yaml").write_text(WORKSPACE)
    (tmp_path / "topologies" / "look.yaml").write_text(TOPOLOGY)
    (tmp_path / "gate.png").write_bytes(PNG)
    return tmp_path


def _mock_of(runtime: WorkspaceRuntime) -> MockModelProvider:
    registry: Any = runtime._provider_registry
    provider = registry.get("mock")
    assert isinstance(provider, MockModelProvider)
    return provider


def _image_blocks(provider: MockModelProvider) -> list[Any]:
    out: list[Any] = []
    for call in provider.calls:
        for message in call.messages:
            if isinstance(message.content, str):
                continue
            out.extend(b for b in message.content if b.type == "image")
    return out


@pytest.mark.asyncio
async def test_the_model_actually_receives_the_bytes(workspace: Path) -> None:
    runtime = WorkspaceRuntime.from_workspace_path(workspace)
    await runtime.run("look", "what is this?", attachments=[{"path": "gate.png"}])

    blocks = _image_blocks(_mock_of(runtime))
    assert len(blocks) == 1
    assert blocks[0].image_media_type == "image/png"
    # The bytes, not a path and not a description of them.
    assert base64.b64decode(blocks[0].image_data) == PNG


@pytest.mark.asyncio
async def test_the_question_still_reaches_the_model(workspace: Path) -> None:
    """An image with the prompt dropped is a quieter failure than no image."""
    runtime = WorkspaceRuntime.from_workspace_path(workspace)
    await runtime.run("look", "what is at the gate?", attachments=[{"path": "gate.png"}])

    text = "".join(
        b.text or ""
        for call in _mock_of(runtime).calls
        for m in call.messages
        if not isinstance(m.content, str)
        for b in m.content
        if b.type == "text"
    )
    assert "what is at the gate?" in text


@pytest.mark.asyncio
async def test_a_run_without_attachments_sends_no_image_blocks(workspace: Path) -> None:
    """The feature must be invisible when unused — this is the path every existing run takes."""
    runtime = WorkspaceRuntime.from_workspace_path(workspace)
    await runtime.run("look", "hello")

    provider = _mock_of(runtime)
    assert _image_blocks(provider) == []
    assert all(isinstance(m.content, str) for c in provider.calls for m in c.messages)


@pytest.mark.asyncio
async def test_inline_bytes_reach_the_model_too(workspace: Path) -> None:
    runtime = WorkspaceRuntime.from_workspace_path(workspace)
    await runtime.run(
        "look",
        "what is this?",
        attachments=[{"data": base64.b64encode(PNG).decode(), "name": "inline.png"}],
    )
    assert len(_image_blocks(_mock_of(runtime))) == 1


@pytest.mark.asyncio
async def test_a_bad_path_fails_the_call_not_the_run(workspace: Path) -> None:
    """Raised before the graph is built, so no MCP server is started and no run is recorded as
    having attempted work."""
    from swarmkit_runtime.attachments import AttachmentError  # noqa: PLC0415

    runtime = WorkspaceRuntime.from_workspace_path(workspace)
    with pytest.raises(AttachmentError):
        await runtime.run("look", "hi", attachments=[{"path": "missing.png"}])

    assert _mock_of(runtime).calls == []


@pytest.mark.asyncio
async def test_the_attachment_is_audited_without_its_bytes(workspace: Path) -> None:
    """The run record answers "what was it shown" — with the digest, not the content."""
    recorded: list[Any] = []

    runtime = WorkspaceRuntime.from_workspace_path(workspace)
    provider: Any = runtime._audit_provider
    original = provider.record

    async def _capture(event: Any) -> None:
        recorded.append(event)
        await original(event)

    provider.record = _capture
    await runtime.run("look", "what is this?", attachments=[{"path": "gate.png"}])

    events = [e for e in recorded if e.event_type == "run.attachments"]
    assert len(events) == 1

    payload = events[0].payload
    assert payload["count"] == 1
    record = payload["attachments"][0]
    assert record["name"] == "gate.png"
    assert record["media_type"] == "image/png"
    assert record["size"] == len(PNG)
    assert record["source"] == "gate.png"
    assert len(record["sha256"]) == 64

    # The content must not be reachable from the audit payload in any encoding.
    serialised = repr(payload)
    assert base64.b64encode(PNG).decode() not in serialised
    assert str(PNG) not in serialised


@pytest.mark.asyncio
async def test_no_attachment_event_when_nothing_was_attached(workspace: Path) -> None:
    recorded: list[Any] = []
    runtime = WorkspaceRuntime.from_workspace_path(workspace)
    provider: Any = runtime._audit_provider
    original = provider.record

    async def _capture(event: Any) -> None:
        recorded.append(event)
        await original(event)

    provider.record = _capture
    await runtime.run("look", "hello")

    assert [e for e in recorded if e.event_type == "run.attachments"] == []


# --- the CLI surface --------------------------------------------------------------------------
#
# Threaded separately from the Python entry point, and a first cut got it wrong: `--attach` was
# read in the command function while `runtime.run` is called from `_execute_run`, so every run with
# an attachment died on `name 'attach' is not defined`. The unit and HTTP tests all passed. Hence
# these — the CLI is its own path and needs its own assertions.


def _cli_workspace(tmp_path: Path) -> Path:
    (tmp_path / "topologies").mkdir()
    (tmp_path / "workspace.yaml").write_text(WORKSPACE)
    (tmp_path / "topologies" / "look.yaml").write_text(TOPOLOGY)
    (tmp_path / "gate.png").write_bytes(PNG)
    return tmp_path


def test_cli_attach_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_MODEL", "mock")
    ws = _cli_workspace(tmp_path)

    result = CliRunner().invoke(
        app, ["run", str(ws), "look", "--input", "what is this?", "--attach", "gate.png"]
    )
    assert result.exit_code == 0, result.output


def test_cli_attach_is_repeatable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_MODEL", "mock")
    ws = _cli_workspace(tmp_path)
    (ws / "second.png").write_bytes(PNG)

    result = CliRunner().invoke(
        app,
        ["run", str(ws), "look", "-i", "these?", "--attach", "gate.png", "--attach", "second.png"],
    )
    assert result.exit_code == 0, result.output


def test_cli_bad_attachment_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 2, not a run failure: nothing executed and nothing was billed."""
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_MODEL", "mock")
    ws = _cli_workspace(tmp_path)

    result = CliRunner().invoke(
        app, ["run", str(ws), "look", "-i", "hi", "--attach", "../escape.png"]
    )
    assert result.exit_code == 2
    assert "escape" in result.output or "escapes the workspace" in result.output


def test_cli_without_attach_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    monkeypatch.setenv("SWARMKIT_MODEL", "mock")
    ws = _cli_workspace(tmp_path)

    result = CliRunner().invoke(app, ["run", str(ws), "look", "-i", "hi"])
    assert result.exit_code == 0, result.output
