"""`swarmkit adapters build` — warm the build-in-sandbox image cache (task #19).

Error paths (unknown adapter, no build block) need no runtime; the success path mocks the build so
it runs without docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from swarmkit_runtime.cli import _cmd_adapters, app
from typer.testing import CliRunner

runner = CliRunner()

_ADAPTER = """apiVersion: swarmkit/v1
kind: ExecutorAdapter
metadata: {{id: my-harness, name: My Harness, description: a workspace adapter for tests}}
spec:
  launch: {{command: [my-harness, run]}}
  stream: {{format: jsonl}}
  event_map: [{{emit: [{{event: result, with: {{status: success}}}}]}}]
  sandbox:
{sandbox}
provenance: {{authored_by: human, version: 0.1.0}}
"""


def _workspace(root: Path, sandbox: str) -> None:
    (root / "workspace.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata: {id: w, name: W}\n"
    )
    adapters = root / "adapters"
    adapters.mkdir(exist_ok=True)
    (adapters / "my-harness.yaml").write_text(_ADAPTER.format(sandbox=sandbox))


def test_unknown_adapter_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["adapters", "build", "nope", str(tmp_path)])
    assert result.exit_code == 1
    assert "Unknown adapter" in result.stdout


def test_adapter_without_build_errors(tmp_path: Path) -> None:
    _workspace(tmp_path, sandbox="    kind: container\n    image: prebuilt:1")
    result = runner.invoke(app, ["adapters", "build", "my-harness", str(tmp_path)])
    assert result.exit_code == 1
    assert "no sandbox.build" in result.stdout


def test_build_success_prints_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(
        tmp_path,
        sandbox="    kind: container\n    build:\n      base: alpine\n      install: ['true']",
    )
    monkeypatch.setattr(_cmd_adapters, "_resolve_runtime", lambda: "docker")

    async def _fake_build(runtime: str, adapter_id: str, build: object, root: Path) -> str:
        return f"swarmkit-harness/{adapter_id}:deadbeef1234"

    monkeypatch.setattr(_cmd_adapters, "build_harness_image", _fake_build)

    result = runner.invoke(app, ["adapters", "build", "my-harness", str(tmp_path)])
    assert result.exit_code == 0
    assert "swarmkit-harness/my-harness:deadbeef1234" in result.stdout
